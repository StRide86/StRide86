from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional

import pandas as pd
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus as urlquote

from news_utils import generate_numeric_id
from text_process_func import raw_text_process_v1

DEFAULT_ALIYUN_HOST = "pc-bp1xg2kr599p0vdu2.pg.polardb.rds.aliyuncs.com"
DEFAULT_ALIYUN_PORT = "5432"
TL_DATABASE = "news_tl_data"
EVENTS_DATABASE = "news_events"
DEFAULT_TABLE_NAME = "news_fx168_live"

_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _default_config_path(home_dir: str) -> str:
    if home_dir.startswith("/data"):
        pyproject_root_dir = os.path.join(home_dir, "py_projects")
    else:
        pyproject_root_dir = os.path.join(home_dir, "workspace", "py_projects")

    return os.path.join(
        pyproject_root_dir,
        "ale_zbc_news_data_crawl_project",
        "config.json",
    )


def get_pg_config(
    config_path: Optional[str] = None,
    home_dir: Optional[str] = None,
) -> Dict[str, Any]:
    home_dir = home_dir or os.environ.get("HOME", "")
    if not home_dir:
        raise EnvironmentError("HOME environment variable is not set.")

    config_path = config_path or _default_config_path(home_dir)
    with open(config_path, "r", encoding="utf-8") as fp:
        config = json.load(fp)

    return config


def _get_aliyun_postgres_settings(config: Dict[str, Any]) -> Dict[str, str]:
    try:
        aliyun_cfg = config["aliyun_postgres"]
        user = aliyun_cfg["user"]
        password = aliyun_cfg["password"]
    except KeyError as exc:
        raise KeyError("Missing aliyun_postgres.user/password in config.") from exc

    host = aliyun_cfg.get("host", DEFAULT_ALIYUN_HOST)
    port = str(aliyun_cfg.get("port", DEFAULT_ALIYUN_PORT))

    return {
        "user": user,
        "password": password,
        "host": host,
        "port": port,
    }


def _build_engine(
    *,
    user: str,
    password: str,
    host: str,
    port: str,
    database: str,
):
    return create_engine(
        f"postgresql://{user}:{urlquote(password)}@{host}:{port}/{database}"
    )


def _validate_table_name(table_name: str) -> str:
    if not _TABLE_NAME_RE.match(table_name):
        raise ValueError(f"Invalid table name: {table_name!r}")
    return table_name


def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except Exception:
        return value is None


def _maybe_string(value: Any) -> Optional[str]:
    if _is_missing(value):
        return None
    return str(value)


def _format_publish_date(value: Any) -> str:
    if _is_missing(value):
        raise ValueError("PUBLISH_DATE is missing.")
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return pd.to_datetime(value).strftime("%Y-%m-%d")


def _format_publish_time(value: Any) -> str:
    if _is_missing(value):
        raise ValueError("PUBLISH_TIME is missing.")
    if hasattr(value, "strftime"):
        return value.strftime("%H:%M:%S")
    return pd.to_datetime(value).strftime("%H:%M:%S")


def is_uid_exists(engine, news_id: str, table_name: str) -> bool:
    table_name = _validate_table_name(table_name)
    with engine.connect() as conn:
        result = conn.execute(
            text(
                f"SELECT EXISTS(SELECT 1 FROM {table_name} WHERE news_id = :news_id)"
            ),
            {"news_id": news_id},
        )

        return bool(result.scalar())


def get_fx168_news_from_tl(
    *,
    limit: int = 5,
    row_index: int = 2,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    config = config or get_pg_config()
    settings = _get_aliyun_postgres_settings(config)

    engine = _build_engine(
        user=settings["user"],
        password=settings["password"],
        host=settings["host"],
        port=settings["port"],
        database=TL_DATABASE,
    )

    sql = text(
        """
        select
            *
        from news_content_alekj
        where "SOURCE" = 'fx168'
          and "INFO_TYPE" = 1
        limit :limit
        """
    )

    try:
        with engine.connect() as conn:
            news = pd.read_sql(sql, conn, params={"limit": limit})
    finally:
        engine.dispose()

    if news.empty:
        raise ValueError("No fx168 news rows returned from TL database.")

    if row_index >= len(news) or row_index < -len(news):
        row_index = len(news) - 1

    return news.iloc[[row_index]]


def tl_one_fx168_news_utc_update(
    one_news: pd.DataFrame,
    *,
    table_name: str = DEFAULT_TABLE_NAME,
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    if one_news is None or one_news.empty:
        raise ValueError("Input news dataframe is empty.")

    config = config or get_pg_config()
    settings = _get_aliyun_postgres_settings(config)

    engine = _build_engine(
        user=settings["user"],
        password=settings["password"],
        host=settings["host"],
        port=settings["port"],
        database=EVENTS_DATABASE,
    )

    try:
        table_name = _validate_table_name(table_name)
        one_news_ = one_news.iloc[0]

        title = one_news_["TITLE"]
        news_publish_date = _format_publish_date(one_news_["PUBLISH_DATE"])
        news_publish_time = _format_publish_time(one_news_["PUBLISH_TIME"])

        news_id = generate_numeric_id(
            f"{title}\n{news_publish_date}\n{news_publish_time}"
        )
        news_id = str(news_id)

        is_existed = is_uid_exists(engine, news_id, table_name)
        if is_existed:
            print(f"fx168 news - {news_id} already exists in {table_name}.")
            return False

        summary = raw_text_process_v1(one_news_["SUMMARY"])
        content = raw_text_process_v1(one_news_["CONTENT"])

        news_publish_dt = one_news_["PUBLISH_DT"]
        if _is_missing(news_publish_dt):
            raise ValueError("PUBLISH_DT is missing.")

        news_publish_dt_utc = news_publish_dt - pd.Timedelta(hours=8)

        url = one_news_["URL"]
        author = one_news_["AUTHOR"]
        org_news_id = _maybe_string(one_news_["ID"])
        news_update_dt = one_news_["UPDATE_TIME"]
        news_keywords = _maybe_string(one_news_["NEWS_KEYWORDS"])
        news_related_themes = one_news_["RELATED_THEMES"]
        l1_type = one_news_["TYPE1"]
        l2_type = one_news_["TYPE2"]
        l3_type = one_news_["TYPE3"]

        news_payload = {
            "news_id": news_id,
            "title": title,
            "summary": summary,
            "content": content,
            "news_publish_dt": news_publish_dt,
            "news_publish_dt_utc": news_publish_dt_utc,
            "news_publish_date": news_publish_date,
            "news_publish_time": news_publish_time,
            "source": "fx168",
            "url": url,
            "author": author,
            "org_news_id": org_news_id,
            "news_keywords": news_keywords,
            "news_related_themes": news_related_themes,
            "l1_type": l1_type,
            "l2_type": l2_type,
            "l3_type": l3_type,
            "l4_type": "",
            "is_vip": 0,
            "news_update_dt": news_update_dt,
        }

        news_df = pd.DataFrame([news_payload])

        try:
            news_df.to_sql(
                table_name,
                con=engine,
                if_exists="append",
                index=False,
            )
            print(f"fx168 news - {news_id} inserted into {table_name}.")
            return True
        except Exception as exc:
            print(
                "fx168 news - "
                f"{news_id} insert to {table_name} exception: {exc.args}"
            )
            return False
    finally:
        engine.dispose()


def main() -> None:
    one_news = get_fx168_news_from_tl()
    tl_one_fx168_news_utc_update(one_news)


if __name__ == "__main__":
    main()
