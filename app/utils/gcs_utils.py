import os
import uuid
import traceback
from google.cloud import storage

def upload_file_to_gcs(file_stream, original_filename, content_type):
    """
    Uploads a file stream to Google Cloud Storage.
    Generates a unique filename using UUID.
    Returns the generated GCS full path or raises an Exception.
    """
    project_id = os.getenv("GCS_PROJECT_ID")
    bucket_name = os.getenv("GCS_BUCKET_NAME")
    bucket_path = os.getenv("GCS_BUCKET_PATH", "drafts")
    
    if not bucket_name:
        raise ValueError("GCS_BUCKET_NAME environment variable not set")

    filename = f"{uuid.uuid4()}_{original_filename}"
    full_path = f"{bucket_path}/{filename}"

    try:
        client = storage.Client(project=project_id)
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(full_path)
        blob.upload_from_file(file_stream, content_type=content_type)
        return full_path
    except Exception as e:
        traceback.print_exc()
        raise Exception(f"Failed to upload to GCS: {str(e)}")
