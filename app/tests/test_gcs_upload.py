import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload a local file to a Google Cloud Storage bucket."
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to the local file that should be uploaded.",
    )
    parser.add_argument(
        "--path",
        default=os.getenv("GCS_BUCKET_PATH"),
        help="Path in the bucket where the file should be uploaded.",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("GCS_BUCKET_NAME"),
        help="Name of the target GCS bucket. Falls back to GCS_BUCKET_NAME from the environment.",
    )
    parser.add_argument(
        "--object-name",
        default=None,
        help="Destination object name in the bucket. Defaults to the local file basename.",
    )
    parser.add_argument(
        "--content-type",
        default=None,
        help="Optional content type for the uploaded object.",
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("GCS_PROJECT_ID"),
        help="Optional Google Cloud project ID. Falls back to GCS_PROJECT_ID from the environment.",
    )
    parser.add_argument(
        "--operation",
        required=True,
        help="The operation to perform (upload, delete).",
    )
    return parser


def upload_file(file_path: Path, bucket_name: str, bucket_path: str, content_type: str | None, project_id: str | None) -> None:
    full_path = f"{bucket_path}/{file_path}"
    
    client = storage.Client(project=project_id)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(full_path)

    blob.upload_from_filename(str(file_path), content_type=content_type)
    
    blob.delete

    print(f"Upload complete: gs://{bucket_name}/{blob.name}")
    print(f"Size: {blob.size} bytes")


def delete_file(args, object_name):
    client = storage.Client(project=args.project_id)
    bucket = client.bucket(args.bucket)
    blob = bucket.blob(f"{args.path}/{object_name}")
    blob.delete()
    print(f"Deleted: gs://{args.bucket}/{args.path}/{object_name}")
    
    
def main() -> int:
    load_dotenv()

    parser = build_parser()
    args = parser.parse_args()

    if not args.bucket:
        parser.error("--bucket is required or set GCS_BUCKET_NAME in your environment.")

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        return 1

    if not file_path.is_file():
        print(f"Error: path is not a file: {file_path}", file=sys.stderr)
        return 1

    object_name = args.object_name or file_path.name

    try:
        if args.operation == "upload":
            upload_file(file_path, args.bucket, args.path, object_name, args.content_type, args.project_id)
        elif args.operation == "delete":
            delete_file(args, object_name)
    except Exception as exc:
        print(f"Upload failed: {exc}", file=sys.stderr)
        return 1

    return 0




if __name__ == "__main__":
    raise SystemExit(main())
