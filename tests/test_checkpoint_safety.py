import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import enrich_catalog
import generate_accessories


class CheckpointSafetyTests(unittest.TestCase):
    def test_missing_checkpoint_stops_before_api_client_is_created(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = os.path.join(directory, "missing.csv")
            for module in (enrich_catalog, generate_accessories):
                with self.subTest(module=module.__name__):
                    with patch.object(module.genai, "Client") as client:
                        with self.assertRaisesRegex(FileNotFoundError, "--allow-new-checkpoint"):
                            if module is enrich_catalog:
                                asyncio.run(module.run("unused.csv", "unused.csv", checkpoint))
                            else:
                                asyncio.run(module.run("unused.csv", "unused.csv", checkpoint, "model"))
                        client.assert_not_called()

    def test_explicit_new_checkpoint_and_existing_checkpoint_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = os.path.join(directory, "checkpoint.csv")
            for module in (enrich_catalog, generate_accessories):
                with self.subTest(module=module.__name__):
                    empty = module.load_checkpoint(checkpoint, allow_new=True)
                    self.assertTrue(empty.empty)
                    empty.to_csv(checkpoint, index=False)
                    self.assertTrue(module.load_checkpoint(checkpoint).empty)
                    os.remove(checkpoint)

    def test_cli_requires_opt_in_even_for_sample_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            for module, extra in (
                (enrich_catalog, []),
                (generate_accessories, ["--sample", "1"]),
            ):
                with self.subTest(module=module.__name__):
                    command = [sys.executable, module.__file__, "--input", "missing-input.csv", *extra]
                    env = {**os.environ, "GEMINI_API_KEY": "test-key"}
                    blocked = subprocess.run(command, cwd=directory, env=env, capture_output=True, text=True)
                    self.assertNotEqual(blocked.returncode, 0)
                    self.assertIn("--allow-new-checkpoint", blocked.stderr)
                    allowed = subprocess.run(
                        [*command, "--allow-new-checkpoint"],
                        cwd=directory, env=env, capture_output=True, text=True,
                    )
                    self.assertNotEqual(allowed.returncode, 0)
                    self.assertIn("missing-input.csv", allowed.stderr)
                    self.assertIn("Starting a new checkpoint", allowed.stderr)


if __name__ == "__main__":
    unittest.main()
