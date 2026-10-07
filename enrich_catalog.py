"""Enrich an electrical wholesale product catalog with Gemini structured output.

Usage:
    python enrich_catalog.py [--input products.csv] [--output enriched_products.csv]

Requires GEMINI_API_KEY in the environment or a .env file. Progress is
checkpointed every 100 rows; re-running resumes from the checkpoint.
"""

import argparse
import asyncio
import json
import logging
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, Field
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)
from tqdm.asyncio import tqdm

MODEL = "gemini-2.5-flash"
CONCURRENCY = 12
CHECKPOINT_EVERY = 100
MAX_ATTEMPTS = 6
ID_COL = "BLAZER ID"
PRODUCT_COL = "PRODUCT"

logger = logging.getLogger("enrich_catalog")

SYSTEM_PROMPT = """You are an electrical wholesale e-commerce taxonomy expert.
You receive a raw product string from a distributor catalog. It may contain
brand abbreviations, manufacturer part numbers, abbreviated sizes/materials,
descriptive text and typos.

Rules:
- Extract and resolve brand codes (e.g. BPT = Bridgeport Fittings, HUB = Hubbell,
  RAB = RAB Lighting, EAT = Eaton/Cutler-Hammer, LEV = Leviton).
- Correct typos in the raw text (e.g. 'SPILT BUSHING' -> 'SPLIT BUSHING').
- Standardize all trade sizes and measurement units
  (e.g. '1/2IN' -> '1/2 Inch (0.50 in)').
- Expand abbreviations (e.g. 'CONN' -> 'Connector').
- Extract the manufacturer part number exactly as written (e.g. 'SB-386NM', '250-RT2').
- Do not invent specifications that cannot reasonably be inferred; omit unknown ones.
- search_keywords must contain 8 to 12 trade terms, contractor jargon, synonyms and tags.
- common_misspellings must contain 3 to 5 common search typos or phonetic variants.
- short_description must be exactly 2 sentences covering function and application.
"""


class EnrichedProduct(BaseModel):
    brand_manufacturer: str = Field(
        description="Decoded manufacturer or brand name, e.g. 'BPT' -> 'Bridgeport Fittings'."
    )
    manufacturer_part_number: str = Field(
        description="Extracted part/catalog number, e.g. 'SB-386NM', '250-RT2'."
    )
    clean_title: str = Field(
        description="Standardized, unabbreviated title, e.g. 'Bridgeport 2-Inch Plastic Split Bushing'."
    )
    normalized_specs: str = Field(
        description="Key technical attributes, e.g. '2 Inch, Plastic, 105°C Rated, cULus Listed'."
    )
    short_description: str = Field(
        description="Clear 2-sentence summary of product function and application."
    )
    primary_category: str = Field(
        description="High-level category, e.g. 'Conduit & Fittings', 'Wiring Devices'."
    )
    sub_category: str = Field(
        description="Specific sub-category, e.g. 'Bushings & Locknuts', 'Compression Connectors'."
    )
    search_keywords: list[str] = Field(
        description="8 to 12 trade terms, contractor jargon, synonyms and search tags."
    )
    common_misspellings: list[str] = Field(
        description="3 to 5 common search typos or phonetic spelling variations."
    )


ENRICHED_FIELDS = list(EnrichedProduct.model_fields)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, genai_errors.APIError):
        code = getattr(exc, "code", None)
        return code in (408, 429) or (isinstance(code, int) and code >= 500)
    return isinstance(
        exc, (asyncio.TimeoutError, ConnectionError, TimeoutError, ValueError)
    )


@retry(
    retry=retry_if_exception(_is_retryable),
    wait=wait_random_exponential(multiplier=2, max=60),
    stop=stop_after_attempt(MAX_ATTEMPTS),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
async def enrich_one(client: genai.Client, product: str) -> EnrichedProduct:
    response = await client.aio.models.generate_content(
        model=MODEL,
        contents=f"Raw product string: {product}",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=EnrichedProduct,
            temperature=0.2,
        ),
    )
    parsed = response.parsed
    if isinstance(parsed, EnrichedProduct):
        return parsed
    if not response.text:
        raise ValueError("Empty or unparsable model response")
    return EnrichedProduct.model_validate_json(response.text)  # ValueError subclass


def _serialize(result: EnrichedProduct) -> dict:
    row = result.model_dump()
    for key in ("search_keywords", "common_misspellings"):
        row[key] = json.dumps(row[key], ensure_ascii=False)
    return row


def load_checkpoint(path: str) -> pd.DataFrame:
    if os.path.exists(path):
        done = pd.read_csv(path, dtype=str, keep_default_na=False)
        return done.drop_duplicates(subset=ID_COL, keep="last")
    return pd.DataFrame(columns=[ID_COL, PRODUCT_COL, *ENRICHED_FIELDS])


def save_atomic(df: pd.DataFrame, path: str) -> None:
    tmp = f"{path}.tmp"
    df.to_csv(tmp, index=False, encoding="utf-8")
    os.replace(tmp, path)


async def run(input_path: str, output_path: str, checkpoint_path: str) -> int:
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        sys.exit("GEMINI_API_KEY is not set (add it to the environment or .env).")

    source = pd.read_csv(input_path, dtype=str, keep_default_na=False)
    missing = {ID_COL, PRODUCT_COL} - set(source.columns)
    if missing:
        sys.exit(f"{input_path} is missing columns: {sorted(missing)}")
    source = source[[ID_COL, PRODUCT_COL]].drop_duplicates(subset=ID_COL, keep="first")

    done = load_checkpoint(checkpoint_path)
    pending = source[~source[ID_COL].isin(set(done[ID_COL]))]
    logger.info("%d rows total, %d done, %d pending", len(source), len(done), len(pending))

    client = genai.Client(api_key=api_key)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    failures = 0

    async def worker(blazer_id: str, product: str):
        nonlocal failures
        async with semaphore:
            try:
                result = await enrich_one(client, product)
            except Exception as exc:  # leave row out of checkpoint so it is retried on resume
                failures += 1
                logger.error("Failed %s (%r): %s", blazer_id, product, exc)
                return None
        return {ID_COL: blazer_id, PRODUCT_COL: product, **_serialize(result)}

    records = pending.to_dict("records")
    with tqdm(total=len(records), desc="Enriching") as bar:
        for start in range(0, len(records), CHECKPOINT_EVERY):
            batch = records[start : start + CHECKPOINT_EVERY]
            tasks = [
                asyncio.ensure_future(worker(r[ID_COL], r[PRODUCT_COL])) for r in batch
            ]
            new_rows = []
            for fut in asyncio.as_completed(tasks):
                row = await fut
                bar.update(1)
                if row:
                    new_rows.append(row)
            if new_rows:
                done = pd.concat([done, pd.DataFrame(new_rows)], ignore_index=True)
                save_atomic(done, checkpoint_path)

    final = source.merge(
        done.drop(columns=[PRODUCT_COL]), on=ID_COL, how="left"
    )
    save_atomic(final, output_path)
    logger.info("Wrote %s (%d failures; re-run to retry them)", output_path, failures)
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="products.csv")
    parser.add_argument("--output", default="enriched_products.csv")
    parser.add_argument("--checkpoint", default="enriched_products.checkpoint.csv")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    failures = asyncio.run(run(args.input, args.output, args.checkpoint))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
