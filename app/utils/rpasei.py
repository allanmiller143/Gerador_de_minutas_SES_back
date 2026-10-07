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
from datetime import datetime, timedelta

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
            fechar_btn.first.click(timeout=2000)
            page.wait_for_timeout(300)
    except Exception:
        pass

    try:
        page.evaluate('''
            const modals = document.querySelectorAll('.sparkling-modal-container, [id^="divInfraSparklingModalContainer"]');
            modals.forEach(modal => modal.remove());
        ''')
        logging.info("ℹ️ Modais de novidades (sparkling) limpos da tela.")
    except Exception:
        pass

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

def _get_browser_launch_options(cfg: Dict[str, Any]) -> Dict[str, Any]:
    options: Dict[str, Any] = {
        "headless": cfg["HEADLESS_MODE"],
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
        ],
    }
    proxy_url = os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("https_proxy") or os.getenv("http_proxy")
    if proxy_url:
        options["proxy"] = {"server": proxy_url}
    return options

def _criar_contexto_browser(browser):
    ctx = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent=DEFAULT_USER_AGENT,
        ignore_https_errors=True,
    )
    # Bloqueia telemetria e CDNs externas (ex: Elastic APM no unpkg.com) que travam o carregamento em redes corporativas com saída restrita
    ctx.route(
        re.compile(r"unpkg\.com|elastic-apm|google-analytics"),
        lambda route: route.abort()
    )
    return ctx

def realizar_login(page, cfg: Dict[str, Any]):
    logging.info("→ Acessando página de login")
    page.goto(cfg["URL_LOGIN"], timeout=cfg["DEFAULT_TIMEOUT"], wait_until="domcontentloaded")
    page.wait_for_selector("input#txtUsuario", timeout=cfg["DEFAULT_TIMEOUT"])
    page.fill("input#txtUsuario", cfg["USUARIO"])
    page.fill("input#pwdSenha", cfg["SENHA"])
    page.select_option("select#selOrgao", cfg["ORGAO"])
    page.click("button:has-text('ACESSAR')")
    page.wait_for_load_state("domcontentloaded")
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
                    #Solução do SyntaxError: usa click(force=True) em vez de querySelector.
                    page.locator(seletor_link_num).first.click(force=True)
                except Exception:
                    href = page.locator(seletor_link_num).first.get_attribute("href")
                    if href:
                        page.evaluate(f"window.open('{href}', '_blank');")
            
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
                linhas.nth(i).click(force=True) 
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

#Atualizado para não procurar demais, estava ocorrendo erros.
def abrir_todas_as_pastas(page):
    try:
        logging.info("📂 Verificando se o processo possui pastas...")
        frame = page.frame(name="ifrArvore")
        if not frame:
            return
        
        botao = frame.locator('a:has(img[title="Abrir todas as Pastas"])')
        botao.wait_for(timeout=1000) 
        
        if botao.count() > 0:
            botao.first.click()
            logging.info("✅ 'Abrir todas as Pastas' clicado.")
            frame.wait_for_timeout(1000)
    except Exception:
        logging.info("ℹ️ Botão de pastas não encontrado (Processo sem pastas). Seguindo...")

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
            if re.search(r"^\d{10}\.\d{6}/\d{4}-\d{2}$", texto) and texto != numero_sei and texto not in processos_anexos:
                processos_anexos.append(texto)
                continue
            if not re.search(r"\(\d{7,}\)$", texto):
                continue
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
                        if anchor.is_visible():
                            url_documento = "https://sei.pe.gov.br/" + anchor.get_attribute("href")
                        else:
                            frame_html = page.frame(name="ifrArvoreHtml")
                            if not frame_html:
                                continue
                            url_documento = frame_html.url
                    else:
                        frame_html = page.frame(name="ifrArvoreHtml")
                        if not frame_html:
                            continue
                        url_documento = frame_html.url

                    arquivo, base64_str = baixar_documento(page, url_documento, texto)

                    resultado.append({
                        "nome": texto,
                        "arquivo": arquivo,
                        "base64": base64_str,
                    })
            except Exception as e:
                logging.warning(f"Erro ao processar documento '{texto}': {e}")

        total_esperado = contar_documentos_arvore(frame, timeout_ms=cfg["DEFAULT_TIMEOUT"])
        
        logging.info(f"🔎 Validação da Extração: Baixados: {len(resultado)} | Na Árvore: {total_esperado}")
        
        #Feito para evitar contar documentos que não consegue baixar.
        if len(resultado) > 0: 
            logging.info(f"✓ Documentos extraídos e aprovados para envio! ({len(resultado)} arquivos)")
            return resultado, processos_anexos
        else:
            frame.wait_for_timeout(1200)

    raise ExtracaoIncompletaError(f"⛔ Extração falhou. Nenhum documento pôde ser baixado.", numero_sei=numero_sei)

# BAIXA UM PROCESSO ESPECÍFICO
def run(numero_processo: str) -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "USUARIO": current_app.config["SEI_USER"],
        "SENHA": current_app.config["SEI_PASS"],
        "ORGAO": current_app.config["SEI_ORGAO"],
        "URL_LOGIN": current_app.config["SEI_URL_LOGIN"],
    }
    headless_cfg = current_app.config.get("HEADLESS", True)
    cfg["HEADLESS_MODE"] = str(headless_cfg).lower() in {"1", "true", "yes", "y"}
    cfg["DEFAULT_TIMEOUT"] = int(current_app.config.get("SEI_TIMEOUT_MS", 60000))
    cfg["MAX_TENTATIVAS"] = int(current_app.config.get("SEI_MAX_TENTATIVAS", 2))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**_get_browser_launch_options(cfg))
        context = _criar_contexto_browser(browser)
        page = context.new_page()
        try:
            realizar_login(page, cfg)
            pesquisar_processo_rapido(page, numero_processo)

            ok, page_atual = garantir_processo_aberto(page, context, numero_processo, timeout_ms=cfg["DEFAULT_TIMEOUT"])
            if not ok:
                return {"status": "erro", "mensagem": "iframe da árvore não encontrado.", "documentos": []}

            abrir_todas_as_pastas(page_atual)

            try:
                documentos, processos_anexos = extrair_documentos_da_arvore(page_atual, cfg, numero_sei=numero_processo)
                return {"status": "ok", "mensagem": "Sucesso", "numeroSei": numero_processo, "documentos": documentos}
            except Exception as e:
                return {"status": "erro", "mensagem": str(e), "numeroSei": numero_processo, "documentos": []}
        finally:
            browser.close()

# LISTA TODOS OS PROCESSOS NA CAIXA DE "RECEBIDOS" (COM PAGINAÇÃO DO SEI)
def buscar_todos_processos_recebidos() -> List[str]:
    cfg: Dict[str, Any] = {
        "USUARIO": current_app.config["SEI_USER"],
        "SENHA": current_app.config["SEI_PASS"],
        "ORGAO": current_app.config["SEI_ORGAO"],
        "URL_LOGIN": current_app.config["SEI_URL_LOGIN"],
    }
    headless_cfg = current_app.config.get("HEADLESS", True)
    cfg["HEADLESS_MODE"] = str(headless_cfg).lower() in {"1", "true", "yes", "y"}
    cfg["DEFAULT_TIMEOUT"] = int(current_app.config.get("SEI_TIMEOUT_MS", 60000))

    lista_processos = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**_get_browser_launch_options(cfg))
        context = _criar_contexto_browser(browser)
        page = context.new_page()
        try:
            realizar_login(page, cfg)
            
            logging.info("→ Escaneando a tabela de Processos Recebidos...")
            
            pagina = 1
            max_paginas = 50  # Suporta até 50 páginas (5.000 processos) com proteção contra loop
            while pagina <= max_paginas:
                page.wait_for_selector("table#tblProcessosRecebidos tbody tr", timeout=cfg["DEFAULT_TIMEOUT"])
                
                # Extrai todos os processos da página atual
                linhas_texto = page.locator("table#tblProcessosRecebidos tbody tr").all_inner_texts()
                novos_nesta_pagina = 0
                for texto in linhas_texto:
                    match = re.search(r"\d{5,}\.\d{6}/\d{4}-\d{2}", texto)
                    if match:
                        numero = match.group(0)
                        if numero not in lista_processos:
                            lista_processos.append(numero)
                            novos_nesta_pagina += 1
                
                logging.info(f"Página {pagina} do SEI processada: {novos_nesta_pagina} novos processos (total acumulado: {len(lista_processos)}).")
                
                # Se não encontrou nenhum processo novo nesta página, encerra
                if novos_nesta_pagina == 0:
                    break

                # Procura o botão de "Próxima Página" no SEI (SEI 3.x e 4.x)
                botao_proxima = page.locator(
                    "a[title*='Próxim'], a:has(img[title*='Próxim']), img[title*='Próxim'], a:has(img[alt*='Próxim']), a.infraPaginacao[title*='Próxim']"
                )
                
                proximo_encontrado = False
                for idx in range(botao_proxima.count()):
                    btn = botao_proxima.nth(idx)
                    if btn.is_visible():
                        logging.info("Navegando para a próxima página do SEI...")
                        btn.click()
                        page.wait_for_load_state("domcontentloaded")
                        page.wait_for_timeout(1000)
                        proximo_encontrado = True
                        pagina += 1
                        break
                
                if not proximo_encontrado:
                    break
                        
            logging.info(f"📋 Sucesso: {len(lista_processos)} processos novos encontrados em todas as páginas.")
        except Exception as e:
            logging.error(f"⛔ Erro ao listar processos da caixa de recebidos: {e}")
        finally:
            browser.close()
            
    return lista_processos


#Entra no processo, preenche o fomrulário básico e cria um documento com a minuta da IA.
def cria_novo_documento(numero_processo: str, minuta: str) -> bool:
    #Configuração básica para entrar no SEI.
    cfg: Dict[str, Any] = {
        "USUARIO": os.getenv("SEI_USER"),
        "SENHA": os.getenv("SEI_PASS"),
        "ORGAO": os.getenv("SEI_ORGAO"),
        "URL_LOGIN": os.getenv("SEI_URL_LOGIN"),
        "DEFAULT_TIMEOUT": int(os.getenv("SEI_TIMEOUT_MS", "60000")),
        "HEADLESS_MODE": os.getenv("HEADLESS", "True").lower() in {"1", "true", "yes", "y"},
    }

    with sync_playwright() as pw:
        #Inicializa o navegador.
        browser = pw.chromium.launch(**_get_browser_launch_options(cfg))
        context = _criar_contexto_browser(browser)
        page = context.new_page()
        
        try:
            #Login e acha o processo.
            realizar_login(page, cfg)
            pesquisar_processo_rapido(page, numero_processo)

            #Verifica se o carregamento da tela do processo foi bem sucedido.
            ok, page_atual = garantir_processo_aberto(page, context, numero_processo, timeout_ms=cfg["DEFAULT_TIMEOUT"])
            if not ok:
                logging.error(f"Não foi possível abrir o processo {numero_processo}.")
                return False

            #Localiza o processo na árvore  e clica nele.
            logging.info("Processo aberto.")
            frame_arvore = page_atual.frame_locator("#ifrArvore")
            raiz_processo = frame_arvore.locator(f"a:has-text('{numero_processo}'), span:has-text('{numero_processo}')").first
            raiz_processo.wait_for(state="visible", timeout=10000)
            raiz_processo.click(force=True) 
            page_atual.wait_for_timeout(3000) 

            #Procura o frame correto dentro da página.
            frame_conteudo = page_atual.frame_locator("#ifrConteudoVisualizacao")
            
            try:
                #Clica no ícone de "Incluir Documento".
                btn_novo_doc = frame_conteudo.locator("[title*='Incluir Documento']").first
                btn_novo_doc.wait_for(state="visible", timeout=10000)
                btn_novo_doc.click(force=True)
                logging.info("Iniciando 'Incluir Documento'.")
            except Exception as e:
                logging.error(f"Falha ao localizar o botão 'Incluir Documento': {e}")
                return False
                
            #Procura o frame correto dentro da página.
            frame_destino = frame_conteudo.frame_locator("#ifrVisualizacao")

            #Preenche o tipo do documento que será inserido.
            try:
                opcao_despacho = frame_destino.locator("a.ancoraOpcao:has-text('GOVPE - Despacho')").first
                opcao_despacho.wait_for(state="visible", timeout=15000)
                opcao_despacho.click()
                logging.info("Opção 'GOVPE - Despacho' selecionada.")
            except Exception as e:
                logging.error(f"Falha ao encontrar o tipo de documento selecionado: {e}")
                return False

            page_atual.wait_for_timeout(3000)

            #Preenche o formulário 
            try:
                logging.info("Preenchendo formulário de 'Gerar Documento'.")

                #Seleciona o Texto Inicial.
                frame_destino.locator("#optNenhum").check(force=True)
                
                #Preenche Descrição.
                frame_destino.locator("#txtDescricao").fill("Resposta a solicitação de insumo.")

                #Preenche o Nível de Acesso.
                frame_destino.locator("#optRestrito").check(force=True)

                #Preenche a Hipótese Legal.
                dropdown_hipotese = frame_destino.locator("#selHipoteseLegal")
                dropdown_hipotese.wait_for(state="visible", timeout=5000)
                dropdown_hipotese.select_option(value="4")
                
            except Exception as e:
                logging.error(f"Erro ao preencher o formulário: {e}")
                return False

            #Lida com a janela pop-up para a edição do documento gerado.
            try:
                logging.info("Começando edição do documento.")

                #Aguarda o SEI abrir a nova janela.
                with page_atual.expect_popup(timeout=15000) as popup_info:
                    frame_destino.locator("#btnSalvar").first.click(force=True)
                nova_janela = popup_info.value
                nova_janela.wait_for_load_state("domcontentloaded")
                nova_janela.wait_for_selector("iframe.cke_wysiwyg_frame", timeout=15000)
                nova_janela.wait_for_timeout(2000)
                
                logging.info("Injetando a minuta formatada no editor.")
                
                frame_corpo = nova_janela.frame_locator("iframe[title='Corpo do Texto']")
                
                frame_corpo.locator("body").evaluate('(el, html) => el.innerHTML = html', minuta)
                
                nova_janela.wait_for_timeout(1000)

                # Finaliza e salva documentos.
                logging.info("Salvando alterações finais no documento.")
                btn_salvar_editor = nova_janela.locator(".cke_button__save_icon >> visible=true").first
                btn_salvar_editor.wait_for(state="visible", timeout=5000)
                btn_salvar_editor.click(force=True)
                
                logging.info("Documento salvo e finalizado com sucesso!")
                nova_janela.wait_for_timeout(1500) 
                
                return True

            except Exception as e:
                logging.error(f"Erro durante a manipulação da janela do editor: {e}")
                return False

        except PlaywrightTimeoutError:
            logging.error("Timeout global! Um ou mais elementos demoraram demais para responder.")
            return False
        except Exception as e:
            logging.error(f"Erro inesperado durante a execução: {e}")
            return False
        finally:
            browser.close()


#Busca por expressões que ditam o prazo do processo em dias.
def extrair_data_vencimento(texto_pdf):
    if not texto_pdf:
        return None

    padrao = r"PRAZO\s+M[ÁA]XIMO\s+DE\s+AT[ÉE]\s+(\d+)"
    match = re.search(padrao, texto_pdf, re.IGNORECASE)

    if match:
        return int(match.group(1)) # Retorna apenas os dias_prazo
    
    return None



if __name__ == "__main__":
    Config = _load_config_from_file()
    app = Flask(__name__)
    app.config.from_object(Config)
    with app.app_context():
        #Roda função 2
        print("Buscando processos na caixa de entrada")
        numeros = buscar_todos_processos_recebidos()
        print(f"Lista de processos: {numeros}")
    
    #Roda a função 1
    """
    with app.app_context():
        numero = sys.argv[1] if len(sys.argv) >= 2 else os.getenv("NUM_SEI")
        numero = "0040609690.000004/2025-81"
        if not numero:
            print("Uso: python app/services/rpasei.py <NUM_SEI>\nOu defina a variável de ambiente NUM_SEI.")
            raise SystemExit(1)
        resultado = run(numero)
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    """