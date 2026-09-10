from app.storage.file_storage import file_storage

def clean_minio():
    if not file_storage.client:
        print("No MinIO client configured.")
        return
    bucket = file_storage.bucket_name
    print(f"Cleaning MinIO bucket: {bucket}...")
    try:
        objs = list(file_storage.client.list_objects(bucket, recursive=True))
        print(f"Found {len(objs)} objects in MinIO.")
        for obj in objs:
            file_storage.client.remove_object(bucket, obj.object_name)
        print(f"Successfully deleted {len(objs)} objects from MinIO bucket '{bucket}'.")
    except Exception as e:
        print(f"Error cleaning MinIO: {e}")

if __name__ == "__main__":
    clean_minio()
