#!/usr/bin/env python3
"""Regression tests for tiling-check.py."""

import hashlib
import importlib.util
import math
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


SCRIPT_PATH = Path(__file__).with_name("tiling-check.py")
SPEC = importlib.util.spec_from_file_location("tiling_check", SCRIPT_PATH)
tiling_check = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(tiling_check)


class EmbedLongBodyTests(unittest.TestCase):
    def test_short_body_keeps_single_request_and_backend_vector(self):
        prompts = []

        def fake_post(url, payload, timeout):
            prompts.append(payload["prompt"])
            return {"embedding": [3.0, 4.0]}

        body = "短い本文"
        with mock.patch.object(
            tiling_check, "_http_post_json", side_effect=fake_post
        ):
            embedding = tiling_check.embed(
                body, "nomic-embed-text", "http://127.0.0.1:11434"
            )

        self.assertEqual(prompts, [body])
        self.assertEqual(embedding, [3.0, 4.0])

    def test_long_japanese_body_is_split_before_embedding(self):
        prompts = []

        def fake_post(url, payload, timeout):
            prompts.append(payload["prompt"])
            return {"embedding": [1.0, 0.0]}

        body = "長文の埋め込み対象です。" * 400
        with mock.patch.object(
            tiling_check, "_http_post_json", side_effect=fake_post
        ):
            tiling_check.embed(body, "nomic-embed-text", "http://127.0.0.1:11434")

        self.assertGreater(
            len(prompts),
            1,
            "2,048トークンを超え得る本文は複数リクエストへ分割する",
        )
        self.assertTrue(
            all(len(prompt.encode("utf-8")) <= 1800 for prompt in prompts)
        )
        self.assertEqual("".join(prompts), body)

    def test_chunk_embeddings_are_length_weighted_and_normalized(self):
        prompts = []

        def fake_post(url, payload, timeout):
            prompt = payload["prompt"]
            prompts.append(prompt)
            if prompt.startswith("a"):
                return {"embedding": [1.0, 0.0]}
            return {"embedding": [0.0, 1.0]}

        body = ("a" * 1800) + ("b" * 900)
        with mock.patch.object(
            tiling_check, "_http_post_json", side_effect=fake_post
        ):
            embedding = tiling_check.embed(
                body, "nomic-embed-text", "http://127.0.0.1:11434"
            )

        self.assertEqual(len(prompts), 2)
        self.assertAlmostEqual(embedding[0], 2 / math.sqrt(5))
        self.assertAlmostEqual(embedding[1], 1 / math.sqrt(5))

    def test_chunk_embedding_dimension_mismatch_is_rejected(self):
        responses = iter(
            [
                {"embedding": [1.0, 0.0]},
                {"embedding": [0.0, 1.0, 2.0]},
            ]
        )

        with mock.patch.object(
            tiling_check,
            "_http_post_json",
            side_effect=lambda url, payload, timeout: next(responses),
        ):
            with self.assertRaisesRegex(RuntimeError, "dimension mismatch"):
                tiling_check.embed(
                    "a" * 2700,
                    "nomic-embed-text",
                    "http://127.0.0.1:11434",
                )

    def test_chunk_http_error_aborts_without_partial_embedding(self):
        responses = iter(
            [
                {"embedding": [1.0, 0.0]},
                urllib.error.HTTPError(
                    "http://127.0.0.1:11434/api/embeddings",
                    500,
                    "Internal Server Error",
                    {},
                    None,
                ),
            ]
        )

        def fake_post(url, payload, timeout):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        with mock.patch.object(
            tiling_check, "_http_post_json", side_effect=fake_post
        ):
            with self.assertRaises(urllib.error.HTTPError):
                tiling_check.embed(
                    "a" * 2700,
                    "nomic-embed-text",
                    "http://127.0.0.1:11434",
                )

    def test_split_prefers_nearby_paragraph_boundary(self):
        body = ("a" * 1000) + "\n\n" + ("b" * 1000)

        chunks = tiling_check.split_embedding_chunks(body)

        self.assertEqual(chunks[0], ("a" * 1000) + "\n\n")
        self.assertEqual("".join(chunks), body)

    def test_chunked_strategy_invalidates_legacy_page_cache(self):
        body = "同じ本文でも埋め込み方式が変われば再計算する"
        model = "nomic-embed-text"
        legacy = hashlib.sha256()
        legacy.update(f"model={model}\n".encode("utf-8"))
        legacy.update(body.encode("utf-8"))

        self.assertNotEqual(
            tiling_check.body_hash(body, model),
            legacy.hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
