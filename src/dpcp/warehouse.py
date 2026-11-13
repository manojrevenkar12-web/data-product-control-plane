"""The Snowflake connection. In an account it is the Snowflake connector; for tests and the simulated world it is
the same connector API on fakesnow (DuckDB underneath). Platform code only sees this class.

Things the emulator cannot do (roles, grants, masking policies, query tags) are created by Terraform in an
account and are not exercised here; see docs/architecture.md.
"""

from __future__ import annotations

import os
from contextlib import ExitStack

import pandas as pd
import snowflake.connector
import snowflake.connector.pandas_tools as pandas_tools

from dpcp.settings import Settings


class Warehouse:
    def __init__(self, settings: Settings):
        cfg = settings["snowflake"]
        self.settings = settings
        self.db = cfg["database"]
        self.emulated = cfg["mode"] == "emulated"
        self._stack = ExitStack()
        if self.emulated:
            import fakesnow

            self._stack.enter_context(fakesnow.patch())
            self.conn = snowflake.connector.connect(database=self.db, schema=cfg["platform_schema"])
        else:
            self.conn = snowflake.connector.connect(
                account=cfg["account"],
                user=os.environ["SNOWFLAKE_USER"],
                authenticator=os.environ.get("SNOWFLAKE_AUTHENTICATOR", "SNOWFLAKE_JWT"),
                private_key_file=os.environ.get("SNOWFLAKE_PRIVATE_KEY_FILE"),
                role=os.environ.get("SNOWFLAKE_ROLE", "DATA_PLATFORM_ADMIN"),
                warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "WH_PLATFORM"),
                database=self.db,
                schema=cfg["platform_schema"],
            )
        self.statements = 0
        self.query_tag: dict = {}

    def schema(self, key: str) -> str:
        return f"{self.db}.{self.settings['snowflake'][key]}"

    def run(self, sql: str, params: tuple | None = None) -> list[tuple]:
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
            self.statements += 1
            return cur.fetchall()  # DDL and DML return a status row; reading description would re-run the query
        finally:
            cur.close()

    def rows(self, sql: str, params: tuple | None = None) -> list[dict]:
        cur = self.conn.cursor(snowflake.connector.DictCursor)
        try:
            cur.execute(sql, params)
            self.statements += 1
            return [{k.lower(): v for k, v in r.items()} for r in cur.fetchall()]
        finally:
            cur.close()

    def load_frame(self, frame: pd.DataFrame, schema_key: str, table: str) -> int:
        frame = frame.rename(columns=str.upper)
        db, schema = self.schema(schema_key).split(".")
        ok, _, rows, _ = pandas_tools.write_pandas(self.conn, frame, table, database=db, schema=schema)
        if not ok:
            raise RuntimeError(f"load into {schema}.{table} failed")
        self.statements += 1
        return rows

    def tag(self, **tag: str) -> None:
        """Query tag for cost attribution. In an account: ALTER SESSION SET QUERY_TAG (the emulator lacks it)."""
        self.query_tag = tag
        if not self.emulated:
            import json

            self.run("alter session set query_tag = %s", (json.dumps(tag, sort_keys=True),))

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            self._stack.close()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


PLATFORM_DDL = """
create schema if not exists {db}.{platform};
create schema if not exists {db}.{stage};
create schema if not exists {db}.{core};
create schema if not exists {db}.{serve};
create table if not exists {db}.{platform}.LOAD_HISTORY (URI varchar, SHA256 varchar, PRODUCT varchar,
  BATCH_ID varchar, LOADED_AT timestamp_ntz);
create table if not exists {db}.{platform}.BATCHES (BATCH_ID varchar, PRODUCT varchar, CONTRACT_VERSION varchar,
  DATA_DATE date, URI varchar, ARRIVED_AT timestamp_ntz, DECIDED_AT timestamp_ntz, STATUS varchar, ROW_COUNT integer,
  FINDINGS varchar);
create table if not exists {db}.{platform}.OUTBOX (EVENT_ID varchar, PRODUCT varchar, KIND varchar, PAYLOAD varchar,
  CREATED_AT timestamp_ntz, SENT_AT timestamp_ntz);
create table if not exists {db}.{platform}.INCIDENTS (INCIDENT_ID varchar, PRODUCT varchar, KIND varchar,
  ROOT_PRODUCT varchar, TEAM varchar, PAGED boolean, OPENED_AT timestamp_ntz, CLOSED_AT timestamp_ntz,
  EVIDENCE varchar);
create table if not exists {db}.{platform}.CHANGES (CHANGE_ID varchar, PRODUCT varchar, FROM_VERSION varchar,
  TO_VERSION varchar, DECIDED_AT timestamp_ntz, STATUS varchar, VIOLATIONS varchar);
create table if not exists {db}.{platform}.ROLE_OWNERS (ROLE_NAME varchar, TEAM varchar);
create table if not exists {db}.{platform}.WAREHOUSE_OWNERS (WAREHOUSE_NAME varchar, TEAM varchar);
create table if not exists {db}.{platform}.USER_OWNERS (USER_NAME varchar, TEAM varchar);
create table if not exists {db}.{platform}.COST_ATTRIBUTION (DAY date, HOUR timestamp_ntz, TEAM varchar,
  WAREHOUSE_NAME varchar, CREDITS float, METHOD varchar);
"""


def bootstrap(wh: Warehouse) -> None:
    s = wh.settings["snowflake"]
    ddl = PLATFORM_DDL.format(
        db=wh.db,
        platform=s["platform_schema"],
        stage=s["staging_schema"],
        core=s["core_schema"],
        serve=s["serve_schema"],
    )
    for stmt in filter(None, (x.strip() for x in ddl.split(";"))):
        wh.run(stmt)
