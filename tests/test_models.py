"""Model selection must not silently initiate unwanted downloads."""

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from trigger_warnings.models import ModelError, resolve_model


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)
        self.download = mock.Mock(return_value=str(self.path))
        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = self.download
        patcher = mock.patch.dict(sys.modules, {"huggingface_hub": hub})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_existing_directory_never_calls_hub(self):
        self.assertEqual(resolve_model(self.path), self.path.resolve())
        self.assertEqual(resolve_model(self.path, local_only=True), self.path.resolve())
        self.download.assert_not_called()

    def test_expands_home_in_local_path(self):
        with mock.patch.dict("os.environ", {"HOME": str(self.path)}):
            self.assertEqual(resolve_model("~/"), self.path.resolve())
        self.download.assert_not_called()

    def test_missing_local_path_and_single_file_do_not_become_hub_ids(self):
        file = self.path / "model.safetensors"
        file.touch()
        for path in (self.path / "missing", file, "./missing-model-folder"):
            with self.subTest(path=path), self.assertRaises(ModelError):
                resolve_model(path)
        self.download.assert_not_called()

    def test_revision_cannot_be_silently_ignored_for_a_local_directory(self):
        with self.assertRaisesRegex(ModelError, "revision"):
            resolve_model(self.path, revision="pinned")
        self.download.assert_not_called()

    def test_local_only_and_revision_reach_cache_resolver(self):
        self.assertEqual(resolve_model("owner/model", revision="pinned", local_only=True),
                         self.path.resolve())
        kwargs = self.download.call_args.kwargs
        self.assertTrue(kwargs["local_files_only"])
        self.assertEqual(kwargs["revision"], "pinned")
        self.assertEqual(kwargs["repo_id"], "owner/model")

    def test_cache_miss_never_retries_with_download_enabled(self):
        self.download.side_effect = OSError("not cached")
        with self.assertRaisesRegex(ModelError, "hf download"):
            resolve_model("owner/model", local_only=True)
        self.download.assert_called_once()
        self.assertTrue(self.download.call_args.kwargs["local_files_only"])

    def test_default_allows_download_through_the_shared_cache(self):
        resolve_model("owner/model")
        self.assertFalse(self.download.call_args.kwargs["local_files_only"])
