import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db.createTable import initialize_database
from sau_backend import app


class MaterialApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_root = Path(self.temp_dir.name)
        self.db_path = self.temp_root / "isolated.db"
        self.media_root = self.temp_root / "media"
        self.media_root.mkdir()
        initialize_database(self.db_path)

        self.original_database_path = app.config["DATABASE_PATH"]
        self.original_media_root = app.config["MEDIA_ROOT"]
        self.original_testing = app.testing
        app.config.update(
            DATABASE_PATH=self.db_path,
            MEDIA_ROOT=self.media_root,
            TESTING=True,
        )
        self.client = app.test_client()

    def tearDown(self):
        app.config.update(
            DATABASE_PATH=self.original_database_path,
            MEDIA_ROOT=self.original_media_root,
            TESTING=self.original_testing,
        )
        self.temp_dir.cleanup()

    def _upload(self, content=b"isolated-image"):
        response = self.client.post(
            "/uploadSave",
            data={"file": (io.BytesIO(content), "cover.png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()["data"]["filepath"]

    def test_material_routes_use_configured_database_and_media_root(self):
        content = b"isolated-image"
        filepath = self._upload(content)

        listed = self.client.get("/getFiles")
        preview = self.client.get("/getFile", query_string={"filename": filepath})
        downloaded = self.client.get(f"/download/{filepath}")

        try:
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(len(listed.get_json()["data"]), 1)
            self.assertEqual(listed.get_json()["data"][0]["file_path"], filepath)
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.data, content)
            self.assertEqual(downloaded.status_code, 200)
            self.assertEqual(downloaded.data, content)
            self.assertIn("attachment", downloaded.headers["Content-Disposition"])
        finally:
            listed.close()
            preview.close()
            downloaded.close()

    def test_delete_material_only_removes_configured_record_and_file(self):
        filepath = self._upload()
        material = self.client.get("/getFiles").get_json()["data"][0]

        deleted = self.client.get("/deleteFile", query_string={"id": material["id"]})

        self.assertEqual(deleted.status_code, 200)
        self.assertFalse((self.media_root / filepath).exists())
        with sqlite3.connect(self.db_path) as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM file_records").fetchone()[0]
        self.assertEqual(remaining, 0)


if __name__ == "__main__":
    unittest.main()
