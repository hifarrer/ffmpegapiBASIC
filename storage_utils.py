import os
import logging

def upload_to_storage(local_file_path, storage_filename):
    """
    Upload a file to storage. On Railway we use the volume (files stay on disk);
    callers get None and use the /download/ URL. On Replit we use Replit Object Storage.
    Returns the public URL if successful, None if failed (caller uses local /download/ URL).
    """
    # Railway: files are already on the volume; no separate object storage
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        logging.info(f"Railway: file already on volume at {local_file_path}, caller will use /download/ URL")
        return None

    try:
        from replit.object_storage import Client
    except ImportError:
        logging.warning("Replit object_storage not available (e.g. on Railway); using local/volume storage")
        return None

    try:
        if not os.path.exists(local_file_path):
            logging.error(f"Local file not found: {local_file_path}")
            return None

        client = Client()
        logging.info(f"Uploading {storage_filename} to Replit App Storage...")
        client.upload_from_filename(storage_filename, local_file_path)

        if os.environ.get('REPLIT_DEPLOYMENT'):
            download_url = f"https://ffmpegapi.net/api/storage/{storage_filename}"
        elif os.environ.get('REPLIT_DEV_DOMAIN'):
            download_url = f"https://{os.environ['REPLIT_DEV_DOMAIN']}/api/storage/{storage_filename}"
        else:
            download_url = f"http://localhost:5000/api/storage/{storage_filename}"

        logging.info(f"Successfully uploaded {storage_filename} to Replit App Storage")
        return download_url

    except Exception as e:
        logging.error(f"Failed to upload {storage_filename} to storage: {str(e)}")
        return None

def get_storage_download_url(storage_filename):
    """
    Get the download URL for a file in storage. On Railway returns None (files on volume).
    """
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        return None
    try:
        from replit.object_storage import Client
        client = Client()
        files = client.list()
        file_exists = any(f.name == storage_filename for f in files)
        if not file_exists:
            logging.error(f"File {storage_filename} not found in storage")
            return None
        if os.environ.get('REPLIT_DEPLOYMENT'):
            return f"https://ffmpegapi.net/api/storage/{storage_filename}"
        elif os.environ.get('REPLIT_DEV_DOMAIN'):
            return f"https://{os.environ['REPLIT_DEV_DOMAIN']}/api/storage/{storage_filename}"
        else:
            return f"http://localhost:5000/api/storage/{storage_filename}"
    except Exception as e:
        logging.error(f"Failed to get download URL for {storage_filename}: {str(e)}")
        return None

def download_from_storage(storage_filename, local_file_path):
    """
    Download a file from storage to local filesystem. On Railway, no-op (files on volume).
    """
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        return False
    try:
        from replit.object_storage import Client
        client = Client()
        data = client.download_as_bytes(storage_filename)
        with open(local_file_path, 'wb') as f:
            f.write(data)
        logging.info(f"Successfully downloaded {storage_filename} from storage")
        return True
    except Exception as e:
        logging.error(f"Failed to download {storage_filename} from storage: {str(e)}")
        return False

def list_storage_files():
    """
    List all files in the storage bucket. On Railway returns [] (files on volume).
    """
    if os.environ.get('RAILWAY_ENVIRONMENT'):
        return []
    try:
        from replit.object_storage import Client
        client = Client()
        return client.list()
    except Exception as e:
        logging.error(f"Failed to list storage files: {str(e)}")
        return []