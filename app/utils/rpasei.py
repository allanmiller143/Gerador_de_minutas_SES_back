import importlib.util
import logging
import re
import os
import sys
import mimetypes
import base64
import json
import unicodedata
from typing import List, Dict, Any, Optional, Tuple
from flask import Flask, current_app
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _load_config_from_file() -> type:
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "config.py"))
    spec = importlib.util.spec_from_file_location("rpasei_config", config_path)
    config_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(config_module)
    return config_module.Config


# EXCEÇÕES
class ExtracaoIncompletaError(Exception):
    def __init__(self, message, numero_sei=None):
        super().__init__(message)
        self.numero_sei = numero_sei

class NenhumProcessoElegivelError(Exception):
    pass


def _sem_acentos(s: str) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")

def _pagina_tem_ifr_arvore(page) -> bool:
    try:
        return page.locator("iframe#ifrArvore").is_visible()
    except Exception:
        return False

def _esperar_ifr_arvore(page, timeout_ms: int) -> bool:
    try:
        page.wait_for_selector("iframe#ifrArvore", timeout=timeout_ms)
        return True
    except Exception:
        return False


# FLUXO DE PÁGINA
def fechar_popup_aviso(page):
    try:
        fechar_btn = page.locator("button:has-text('Fechar'), a:has-text('Fechar')")
        if fechar_btn.count() > 0:
            fechar_btn.first.click()
            page.wait_for_timeout(300)
            logging.info("ℹ️ Pop-up 'AVISO IMPORTANTE' fechado.")
    except Exception:
        pass

def realizar_login(page, cfg: Dict[str, Any]):
    logging.info("→ Acessando página de login")
    page.goto(cfg["URL_LOGIN"], timeout=cfg["DEFAULT_TIMEOUT"])
    page.fill("input#txtUsuario", cfg["USUARIO"])
    page.fill("input#pwdSenha", cfg["SENHA"])
    page.select_option("select#selOrgao", cfg["ORGAO"])
    page.click("button:has-text('ACESSAR')")
    page.wait_for_load_state("networkidle")
    fechar_popup_aviso(page)
    page.wait_for_selector("input#txtPesquisaRapida", timeout=cfg["DEFAULT_TIMEOUT"])
    logging.info("✓ Login concluído, campo de pesquisa rápida disponível.")

def pesquisar_processo_rapido(page, numeroSei: str):
    try:
        logging.info(f"→ Pesquisando processo: {numeroSei}")
        page.fill("input#txtPesquisaRapida", "")
        page.fill("input#txtPesquisaRapida", numeroSei)
        page.press("input#txtPesquisaRapida", "Enter")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(700)
        logging.info("✓ Pesquisa enviada com sucesso")
    except Exception as e:
        logging.error(f"⛔ Erro ao realizar pesquisa rápida: {e}")

def abrir_resultado_pos_pesquisa(page, context, numeroSei: str, timeout_ms: int) -> Tuple[bool, Any]:
    seletor_link_num = f"a:has-text('{numeroSei}')"
    try:
        if page.locator(seletor_link_num).count() > 0:
            with context.expect_page() as nova_pg_info:
                try:
                    page.locator(seletor_link_num).first.click()
                except Exception:
                    page.evaluate("(sel)=>document.querySelector(sel)?.click()", seletor_link_num)
            try:
                nova_pg = nova_pg_info.value
                nova_pg.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                logging.info("↗️ Resultado abriu em nova aba.")
                return True, nova_pg
            except Exception:
                page.wait_for_timeout(500)
                logging.info("↩️ Resultado abriu na mesma aba.")
                return True, page
        else:
            logging.info("ℹ️ Link do número não encontrado na página de resultados.")
            return False, page
    except Exception as e:
        logging.warning(f"⚠️ Falha ao abrir resultado da pesquisa: {e}")
        return False, page

def abrir_processo_por_numero_recebidos(page, numeroSei: str, timeout_ms: int) -> bool:
    try:
        page.wait_for_selector("table#tblProcessosRecebidos tbody tr", timeout=5000)
    except Exception:
        logging.info("ℹ️ Tabela de Processos Recebidos não está visível.")
        return False

    try:
        linhas = page.locator("table#tblProcessosRecebidos tbody tr")
        total = linhas.count()
        logging.info(f"→ Verificando {total} linha(s) da tabela de recebidos")
        for i in range(total):
            texto = linhas.nth(i).inner_text()
            if numeroSei in texto:
                linhas.nth(i).click()
                if _esperar_ifr_arvore(page, timeout_ms=timeout_ms):
                    logging.info(f"✓ Processo '{numeroSei}' aberto via Recebidos")
                    return True
                break
        logging.warning(f"⚠️ Processo {numeroSei} não encontrado na tabela.")
        return False
    except Exception as e:
        logging.error(f"⛔ Erro ao tentar abrir processo via Recebidos: {e}")
        return False

def garantir_processo_aberto(page, context, numeroSei: str, timeout_ms: int) -> Tuple[bool, Any]:
    if _pagina_tem_ifr_arvore(page):
        logging.info("✓ Processo já aberto (ifrArvore visível).")
        return True, page

    logging.info("→ Tentando abrir o resultado da pesquisa (link do número)...")
    abriu, pg = abrir_resultado_pos_pesquisa(page, context, numeroSei, timeout_ms=timeout_ms)
    if abriu and _esperar_ifr_arvore(pg, timeout_ms=timeout_ms):
        return True, pg

    logging.info("→ Fallback: tentando abrir via 'Processos Recebidos'...")
    alvo = pg if abriu else page
    ok = abrir_processo_por_numero_recebidos(alvo, numeroSei, timeout_ms=timeout_ms)
    return (ok and _pagina_tem_ifr_arvore(alvo)), alvo

def abrir_todas_as_pastas(page):
    try:
        logging.info("📂 Procurando botão 'Abrir todas as Pastas'...")
        frame = page.frame(name="ifrArvore")
        if not frame:
            logging.warning("❌ Iframe 'ifrArvore' não encontrado (abrir_todas_as_pastas).")
            return
        botao = frame.locator('a:has(img[title="Abrir todas as Pastas"])')
        botao.wait_for(timeout=5000)
        if botao.count() > 0:
            botao.first.click()
            logging.info("✅ 'Abrir todas as Pastas' clicado.")
            frame.wait_for_timeout(1500)
        else:
            logging.warning("⚠️ Botão não encontrado.")
    except Exception as e:
        logging.warning(f"⚠️ Erro ao clicar no botão: {e}")

def contar_documentos_arvore(frame, timeout_ms: int) -> int:
    try:
        frame.wait_for_selector(".infraArvore", timeout=timeout_ms)
        documentos = frame.locator(".infraArvore span")
        total = documentos.count()
        validos = 0
        for i in range(total):
            no = documentos.nth(i)
            try:
                texto = no.inner_text().strip()
            except Exception:
                continue
            if not texto or texto in ["Fechar", "Link para Acesso Direto"]:
                continue
            if not re.search(r"\(\d{7,}\)$", texto):
                continue
            try:
                cancelado = no.locator("..").get_attribute("href") == "about:blank"
                if no.is_visible() and not cancelado:
                    validos += 1
            except Exception:
                continue
        logging.debug(f"📑 Documentos visíveis na árvore: {validos} (total spans: {total})")
        return validos
    except Exception as e:
        logging.warning(f"⚠️ Falha ao contar documentos da árvore: {e}")
        return 0

def baixar_documento(page, url_documento: str, nome_documento: str):
    resposta = page.request.get(url_documento)
    tipo_conteudo = (resposta.headers or {}).get("content-type", "")
    nome_limpo = re.sub(r'[\\/:*?"<>|]', '_', nome_documento).strip()
    ext = ".pdf" if "application/pdf" in tipo_conteudo else (mimetypes.guess_extension(tipo_conteudo.split(";")[0]) or ".bin")

    filename = f"{nome_limpo}{ext}"
    conteudo_base64 = base64.b64encode(resposta.body()).decode("utf-8")
    logging.info(f"✓ Documento '{nome_documento}' extraído")
    return filename, conteudo_base64

def extrair_documentos_da_arvore(page, cfg: Dict[str, Any], numero_sei: Optional[str] = None) -> Tuple[List[Dict[str, Any]], List[str]]:
    frame = page.frame(name="ifrArvore")
    if not frame:
        raise ExtracaoIncompletaError("iframe ifrArvore não disponível", numero_sei=numero_sei)

    tentativa = 0
    resultado: List[Dict[str, Any]] = []

    while tentativa < cfg["MAX_TENTATIVAS"]:
        tentativa += 1
        logging.info(f"🔁 Tentativa de extração #{tentativa}")
        resultado.clear()
        textos_processados = set()
        processos_anexos = []

        for _ in range(3):
            botoes = frame.locator(".infraArvore .infraArvoreIconeMaisMenos")
            count = botoes.count()
            for b in range(count):
                try: botoes.nth(b).click()
                except Exception: pass
            frame.wait_for_timeout(300)

        documento_nos = frame.locator(".infraArvore span")
        count_docs = documento_nos.count()
        for j in range(count_docs):
            no = documento_nos.nth(j)
            try:
                texto = no.inner_text().strip()
            except Exception:
                continue

            if not texto or texto in textos_processados:
                continue
            if texto in ["Fechar", "Link para Acesso Direto"]:
                continue
            #checa se o item eh um processo anexo
            if re.search(r"^\d{10}\.\d{6}/\d{4}-\d{2}$", texto) and texto != numero_sei and texto not in processos_anexos:
                processos_anexos.append(texto)
                continue
            #checa se o item eh o proprio processo na arvore
            if not re.search(r"\(\d{7,}\)$", texto):
                continue
            #checa se o documento foi cancelado
            if no.locator("..").get_attribute("href") == "about:blank":
                continue

            textos_processados.add(texto)
            try:
                if no.is_visible():
                    no.click()
                    page.wait_for_timeout(700)
                    url_documento = ""
                    if cfg["HEADLESS_MODE"]:
                        frame_arquivo = page.frame(name="ifrVisualizacao")
                        div_link = frame_arquivo.locator("#divArvoreInformacao")
                        anchor = div_link.locator(".ancoraVisualizacaoArvore")
                        if anchor.is_visible(): #trata-se de um documento binario
                            url_documento = "https://sei.pe.gov.br/" + anchor.get_attribute("href")
                        else: #trata-se de um documento html
                            frame_html = page.frame(name="ifrArvoreHtml")
                            if not frame_html:
                                logging.debug("⚠️ ifrArvoreHtml não disponível após clique.")
                                continue
                            url_documento = frame_html.url
                    else:
                        frame_html = page.frame(name="ifrArvoreHtml")
                        if not frame_html:
                            logging.debug("⚠️ ifrArvoreHtml não disponível após clique.")
                            continue
                        url_documento = frame_html.url

                    arquivo, base64_str = baixar_documento(page, url_documento, texto)
                    resultado.append({
                        "nome": texto,
                        "arquivo": arquivo,
                        "base64": base64_str
                    })
            except Exception as e:
                logging.warning(f"Erro ao processar documento '{texto}': {e}")

        total_esperado = contar_documentos_arvore(frame, timeout_ms=cfg["DEFAULT_TIMEOUT"])
        if total_esperado == 0:
            logging.warning("⚠️ Nenhum documento esperado detectado na árvore.")
        if len(resultado) >= total_esperado and total_esperado > 0:
            logging.info(f"✓ Todos os documentos foram extraídos com sucesso ({len(resultado)})")
            return resultado, processos_anexos
        else:
            logging.error(f"⛔ Extração incompleta: esperados {total_esperado}, extraídos {len(resultado)}")
            frame.wait_for_timeout(1200)

    raise ExtracaoIncompletaError(
        f"⛔ Extração falhou após {tentativa} tentativa(s): esperados {total_esperado}, extraídos {len(resultado)}",
        numero_sei=numero_sei
    )

def gerar_pdf_processo(page, cfg):
    try:
        logging.info("→ Gerando PDF do processo...")
        download_path = None
        frame_visualizacao = page.frame(name="ifrVisualizacao")
        if not frame_visualizacao:
            logging.warning("⚠️ Iframe de visualização não encontrado.")
            return None

        try:
            frame_visualizacao.wait_for_selector("a[href*='procedimento_gerar_pdf']", timeout=cfg["DEFAULT_TIMEOUT"])
        except Exception:
            logging.warning("⚠️ Hyperlink de geração de PDF não encontrado dentro do iframe.")
            return None

        frame_visualizacao.click("a[href*='procedimento_gerar_pdf']")
        logging.info("✓ Hyperlink de geração de PDF clicado.")
        frame_visualizacao.wait_for_load_state("networkidle")

        # If the iframe navigated to a new document, re-resolve it before locating the button.
        page.wait_for_selector("iframe#ifrVisualizacao", timeout=cfg["DEFAULT_TIMEOUT"])
        frame_visualizacao = page.frame(name="ifrVisualizacao")
        if not frame_visualizacao:
            logging.warning("⚠️ Iframe de visualização não pôde ser recuperado após navegação.")
            return None

        try:
            frame_visualizacao.wait_for_selector("button[name='btnGerar']", timeout=cfg["DEFAULT_TIMEOUT"])
        except Exception:
            logging.warning("⚠️ Botão btnGerar não apareceu dentro do iframe após navegação.")
            return None

        botao_gerar = frame_visualizacao.query_selector("button[name='btnGerar']")
        if botao_gerar:
            with page.expect_download() as download_info:
                frame_visualizacao.click("button[name='btnGerar']")
            download = download_info.value
            download_path = os.path.join("/tmp", download.suggested_filename)
            download.save_as(download_path)
            logging.info(f"✓ Botão Gerar clicado e arquivo salvo em {download_path}")
        else:
            logging.warning("⚠️ Botão Gerar não encontrado dentro do iframe.")

        return download_path
    except Exception as e:
        logging.error(f"⛔ Erro ao tentar gerar PDF: {e}")
        return None


# FUNÇÃO PRINCIPAL
def run(numero_processo: str) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "USUARIO": current_app.config["SEI_USER"],
        "SENHA": current_app.config["SEI_PASS"],
        "ORGAO": current_app.config["SEI_ORGAO"],
        "URL_LOGIN": current_app.config["SEI_URL_LOGIN"],
    }

    headless_cfg = current_app.config.get("HEADLESS", True)
    if isinstance(headless_cfg, str):
        cfg["HEADLESS_MODE"] = headless_cfg.lower() in {"1", "true", "yes", "y"}
    else:
        cfg["HEADLESS_MODE"] = bool(headless_cfg)

    cfg["DEFAULT_TIMEOUT"] = int(current_app.config.get("SEI_TIMEOUT_MS", 60000))
    cfg["MAX_TENTATIVAS"] = int(current_app.config.get("SEI_MAX_TENTATIVAS", 2))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=cfg["HEADLESS_MODE"], args=["--start-maximized"])
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        page.set_extra_http_headers({"sec-ch-ua": '"Chromium";v="125", "Not.A/Brand";v="24"'})
        try:
            realizar_login(page, cfg)
            pesquisar_processo_rapido(page, numero_processo)
            gerar_pdf_processo(page, cfg)

            ok, page_atual = garantir_processo_aberto(
                page, context, numero_processo, timeout_ms=cfg["DEFAULT_TIMEOUT"]
            )
            if not ok:
                logging.info("🚫 ifrArvore não encontrado após tentativas (resultado e recebidos).")
                return {
                    "status": "erro",
                    "mensagem": "iframe da árvore não encontrado após abrir o processo.",
                    "numeroSei": numero_processo,
                    "documentos": [],
                    "processosAnexos": []
                }

            abrir_todas_as_pastas(page_atual)

            try:
                documentos, processos_anexos = extrair_documentos_da_arvore(page_atual, cfg, numero_sei=numero_processo)
                status = "ok"
                mensagem = "Documentos extraídos com sucesso."
            except ExtracaoIncompletaError as ee:
                status = "parcial"
                mensagem = str(ee)
                documentos = []
                processos_anexos = []
            except Exception as e:
                status = "erro"
                mensagem = f"Falha ao extrair documentos: {e}"
                documentos = []
                processos_anexos = []

            payload = {
                "status": status,
                "mensagem": mensagem,
                "numeroSei": numero_processo,
                "documentos": documentos,
                "processosAnexos": processos_anexos
            }
            logging.info("✓ Automação concluída.")
            return payload

        except Exception as e:
            logging.error(f"⛔ Erro inesperado durante a execução: {e}")
            return {
                "status": "erro",
                "mensagem": f"Erro inesperado: {e}",
                "numeroSei": numero_processo,
                "documentos": [],
                "processosAnexos": []
            }
        finally:
            logging.info("→ Fechando navegador em 2 segundos…")
            try:
                page.wait_for_timeout(2000)
            except Exception:
                pass
            browser.close()


def get_sei_files(numero_processo: str) -> List[Dict[str, Any]]:
    payload = run(numero_processo)

    if payload.get("status") == "erro":
        raise RuntimeError(payload.get("mensagem", json.dumps(payload, ensure_ascii=False)))

    return payload.get("documentos", [])


if __name__ == "__main__":
    Config = _load_config_from_file()
    app = Flask(__name__)
    app.config.from_object(Config)

    with app.app_context():
        numero = sys.argv[1] if len(sys.argv) >= 2 else os.getenv("NUM_SEI")
        numero = "0040609690.000004/2025-81"
        if not numero:
            print("Uso: python app/services/rpasei.py <NUM_SEI>\nOu defina a variável de ambiente NUM_SEI.")
            raise SystemExit(1)
        resultado = run(numero)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
