#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A股主线题材热度矩阵每日自动复盘生成器 (自包含版)
================================================================
数据来源: https://lianban.net/mainline.html
功能:
  1. 每日定时抓取连板网题材热度矩阵数据 (含涨停个股明细)
  2. 本地 JSON 历史库自动累积，保留最近 15 个交易日
  3. 生成自包含 HTML 文件 (以最新交易日期命名，如 20260911.html)
  4. 题材热度矩阵 + 涨停棱镜走势图 (右副轴显示情绪温度)
  5. 当日涨停行下方增加情绪周期行 (冰点/退潮/降温/修复/升温/高潮)
  6. 选中板块全局联动高亮
  7. 选中板块某日涨停个股明细表
  8. 当日全部关联个股多列紧凑平铺
  9. 每只个股代码/名称链接到东方财富概念行情页

用法:
  python mainline_tracker.py --now            # 立即执行一次
  python mainline_tracker.py                  # 启动守护进程 (每日 19:30)
  python mainline_tracker.py --time 18:00     # 指定每日执行时间
  python mainline_tracker.py --outdir ./out   # 指定输出目录
"""

import os
import sys
import re
import json
import time
import glob
import argparse
import datetime
import urllib.request
import urllib.error

# =====================================================================
# 配置
# =====================================================================
TARGET_URL = "https://lianban.net/mainline.html"
DEFAULT_RUN_TIME = "19:30"
TARGET_DAYS = 15
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = SCRIPT_DIR
HISTORY_FILE = os.path.join(SCRIPT_DIR, "mainline_history.json")

# HTTP 请求头
HTTP_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'identity',
}


# =====================================================================
# 工具函数
# =====================================================================
def log(msg):
    """带时间戳的日志输出"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def clean_sector_name(name):
    """去除板块名称中的括号及其内部内容
    例: 算力(液冷) -> 算力, 化工(PTFE) -> 化工
    """
    if not name:
        return ""
    cleaned = re.sub(r'[\(（].*?[\)）]', '', str(name)).strip()
    return cleaned if cleaned else str(name).strip()


def clean_emotion_name(raw):
    """清洗情绪周期名称: '情绪冰点期' -> '冰点', '市场退潮期' -> '退潮'"""
    if not raw:
        return ""
    s = str(raw).strip()
    s = re.sub(r'^(?:市场|情绪)+', '', s)
    s = re.sub(r'期+$', '', s)
    return s.strip() if s.strip() else str(raw).strip()


def get_week_cn(date_str):
    """YYYY-MM-DD -> 中文星期"""
    try:
        dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
        weeks = ["一", "二", "三", "四", "五", "六", "日"]
        return weeks[dt.weekday()]
    except Exception:
        return ""


def get_market(code):
    """根据股票代码判断市场前缀 (sh/sz/bj)"""
    code = str(code).strip()
    if code.startswith(('60', '688', '689', '900')):
        return 'sh', '沪市'
    elif code.startswith(('00', '30', '200')):
        return 'sz', '深市'
    elif code.startswith(('8', '4', '920')):
        return 'bj', '北交所'
    else:
        if code.isdigit() and int(code[:3]) >= 600:
            return 'sh', '沪市'
        return 'sz', '深市'


def get_eastmoney_url(code):
    """生成东方财富概念行情链接"""
    code = str(code).strip()
    prefix, _ = get_market(code)
    return f"https://quote.eastmoney.com/concept/{prefix}{code}.html"


# =====================================================================
# 本地历史数据库
# =====================================================================
def load_history():
    """读取本地历史数据 JSON"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"读取历史数据失败: {e}")
    return _empty_db()


def _empty_db():
    return {
        "trading_dates": [],
        "date_labels": [],
        "day_total_zts": {},
        "day_emotions": {},
        "day_emotion_temps": {},
        "daily_rankings": {},
        "block_stocks_data": {},
        "day_all_stocks": {}
    }


def save_history(db):
    """保存历史数据到本地 JSON"""
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
        log(f"历史库已更新: {HISTORY_FILE} (累计 {len(db.get('trading_dates', []))} 个交易日)")
    except Exception as e:
        log(f"保存历史数据失败: {e}")


def merge_into_history(db, new_data):
    """将新抓取的数据合并进历史数据库 (只保留最近 N 天)"""
    # 合并交易日期
    dates_set = set(db.get("trading_dates", []))
    for d in new_data.get("trading_dates", []):
        dates_set.add(d)

    # 合并日期标签
    label_map = {item[0]: item for item in db.get("date_labels", [])}
    for item in new_data.get("date_labels", []):
        label_map[item[0]] = item

    # 合并每日涨停总数
    db.setdefault("day_total_zts", {}).update(new_data.get("day_total_zts", {}))

    # 合并情绪数据
    db.setdefault("day_emotions", {}).update(new_data.get("day_emotions", {}))
    db.setdefault("day_emotion_temps", {}).update(new_data.get("day_emotion_temps", {}))

    # 合并每日排名
    db.setdefault("daily_rankings", {})
    for d, r_list in new_data.get("daily_rankings", {}).items():
        db["daily_rankings"][d] = r_list

    # 合并板块个股明细
    db.setdefault("block_stocks_data", {})
    for sec, dmap in new_data.get("block_stocks_data", {}).items():
        if sec not in db["block_stocks_data"]:
            db["block_stocks_data"][sec] = {}
        for d, slist in dmap.items():
            db["block_stocks_data"][sec][d] = slist

    # 合并当日全部个股
    db.setdefault("day_all_stocks", {})
    for d, slist in new_data.get("day_all_stocks", {}).items():
        db["day_all_stocks"][d] = slist

    # 只保留最近 TARGET_DAYS*2 天的数据 (多保留一倍以防补缺)
    all_sorted = sorted(list(dates_set), reverse=True)
    keep_dates = set(all_sorted[:TARGET_DAYS * 2])

    db["trading_dates"] = all_sorted[:TARGET_DAYS * 2]
    db["date_labels"] = [label_map[d] for d in db["trading_dates"] if d in label_map]

    # 清理过期数据
    for key in ["day_total_zts", "day_emotions", "day_emotion_temps"]:
        db[key] = {d: v for d, v in db.get(key, {}).items() if d in keep_dates}
    db["daily_rankings"] = {d: v for d, v in db.get("daily_rankings", {}).items() if d in keep_dates}
    db["day_all_stocks"] = {d: v for d, v in db.get("day_all_stocks", {}).items() if d in keep_dates}
    # block_stocks_data 需要清理日期维度
    for sec in list(db.get("block_stocks_data", {}).keys()):
        db["block_stocks_data"][sec] = {
            d: v for d, v in db["block_stocks_data"][sec].items() if d in keep_dates
        }
        if not db["block_stocks_data"][sec]:
            del db["block_stocks_data"][sec]

    return db


# =====================================================================
# 数据抓取与解析
# =====================================================================
def fetch_html(url=TARGET_URL, retries=3, delay=5):
    """抓取远程 HTML 内容"""
    req = urllib.request.Request(url, headers=HTTP_HEADERS)
    for attempt in range(1, retries + 1):
        try:
            log(f"请求 {url} (第 {attempt} 次)...")
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
            log(f"成功获取 HTML ({len(html)} 字节)")
            return html
        except Exception as e:
            log(f"请求失败: {e}")
            if attempt < retries:
                time.sleep(delay)
            else:
                raise
    return ""


def parse_mainline_html(html):
    """解析连板网 mainline.html，提取所有结构化数据
    
    返回结构:
      trading_dates: [date_str, ...]          # 交易日期列表 (新到旧)
      date_labels: [[full_date, mm-dd, weekday], ...]
      day_total_zts: {date: int}               # 每日涨停总数
      day_emotions: {date: str}               # 情绪周期 (冰点/退潮/...)
      day_emotion_temps: {date: int}           # 情绪温度 (0-100)
      daily_rankings: {date: [{sector, count, stocks}, ...]}
      block_stocks_data: {sector: {date: [stock_dict, ...]}}
      day_all_stocks: {date: [stock_dict, ...]}
    """
    # ---- 1. 提取 MTXM JavaScript 变量 (板块 -> 日期 -> 个股列表) ----
    mtxm_match = re.search(r'var MTXM\s*=\s*(\{.*?\});', html, re.DOTALL)
    if not mtxm_match:
        raise ValueError("未能提取 MTXM 数据")
    raw_mtxm = json.loads(mtxm_match.group(1))

    # 清洗板块名并合并
    mtxm = {}
    for raw_sec, date_map in raw_mtxm.items():
        c_sec = clean_sector_name(raw_sec)
        if not c_sec:
            continue
        if c_sec not in mtxm:
            mtxm[c_sec] = {}
        for d, slist in date_map.items():
            if d not in mtxm[c_sec]:
                mtxm[c_sec][d] = []
            existing_codes = {s[0] for s in mtxm[c_sec][d]}
            for stk in slist:
                if stk[0] not in existing_codes:
                    mtxm[c_sec][d].append(stk)
                    existing_codes.add(stk[0])

    # ---- 2. 提取表格日期表头 ----
    table_match = re.search(r'<table class="mtx[^"]*"[^>]*>(.*?)</table>', html, re.DOTALL)
    if not table_match:
        raise ValueError("未找到矩阵表格")
    table_html = table_match.group(1)

    dates_raw = re.findall(r'<th data-d="([^"]+)">([^<]+)<br><i>([^<]+)</i></th>', table_html)
    if not dates_raw:
        dates_raw = re.findall(r'<th data-d="([^"]+)">([^<]+)</th>', table_html)
        dates_raw = [(d[0], d[1], get_week_cn(d[0])) for d in dates_raw]

    trading_dates = [d[0] for d in dates_raw]

    # ---- 3. 提取当日涨停总数行 ----
    day_total_zts = {}
    tot_match = re.search(r'<tr[^>]*class="[^"]*mtot[^"]*"[^>]*>(.*?)</tr>', table_html, re.DOTALL)
    if not tot_match:
        tot_match = re.search(r'<tr[^>]*>.*?<th[^>]*>.*?(?:涨停|总计).*?</th>(.*?)</tr>',
                              table_html, re.DOTALL)
    if tot_match:
        tds = re.findall(r'<td[^>]*>(.*?)</td>', tot_match.group(0), re.DOTALL)
        for idx, td in enumerate(tds):
            val = re.sub(r'<[^>]+>', '', td).strip()
            if idx < len(trading_dates):
                d = trading_dates[idx]
                day_total_zts[d] = int(val) if val.isdigit() else 0

    # ---- 4. 从 .mlt 块提取历史交易日数据 ----
    mlt_blocks = re.findall(r'<div class="mlt">(.*?)</div>\s*</div>', html, re.DOTALL)
    for b in mlt_blocks:
        d_m = re.search(r'/days/(\d{4}-\d{2}-\d{2})\.html', b)
        cnt_m = re.search(r'(\d+)\s*只', b)
        if d_m and cnt_m:
            d = d_m.group(1)
            cnt = int(cnt_m.group(1))
            if d not in day_total_zts or day_total_zts.get(d, 0) == 0:
                day_total_zts[d] = cnt

    # ---- 5. 提取情绪状态与温度 ----
    day_emotions = {}
    day_emotion_temps = {}
    emo_matches = re.findall(
        r'/days/(\d{4}-\d{2}-\d{2})\.html[^>]*>.*?</a>\s*<span>([^<\xb7\u00b7\u2022]+)[\xb7\u00b7\u2022]\s*(\d+)\xb0?\s*</span>',
        html
    )
    for d, raw_status, temp_val in emo_matches:
        day_emotions[d] = clean_emotion_name(raw_status)
        day_emotion_temps[d] = int(temp_val)

    # ---- 6. 生成每日排名 ----
    daily_rankings = {}
    for d in trading_dates:
        day_sectors = []
        for sector, date_map in mtxm.items():
            if d in date_map:
                stk_list = date_map[d]
                day_sectors.append({
                    "sector": sector,
                    "count": len(stk_list),
                    "stocks": stk_list
                })
        day_sectors.sort(key=lambda x: (-x["count"], x["sector"]))
        daily_rankings[d] = day_sectors

    # ---- 7. 生成板块个股明细 ----
    block_stocks_data = {}
    for sec, dates_map in mtxm.items():
        block_stocks_data[sec] = {}
        for d, stks in dates_map.items():
            stk_list = []
            for idx, stk in enumerate(stks, 1):
                code = stk[0]
                name = stk[1].strip() if len(stk) > 1 else ""
                prefix, mkt_name = get_market(code)
                stk_list.append({
                    "序号": idx,
                    "交易日期": d,
                    "板块名称": sec,
                    "股票代码": code,
                    "股票名称": name,
                    "东财链接": get_eastmoney_url(code),
                    "市场类型": mkt_name
                })
            block_stocks_data[sec][d] = stk_list

    # ---- 8. 生成当日全部关联个股 ----
    day_all_stocks = {}
    for d in trading_dates:
        stk_map = {}
        for sec_info in daily_rankings.get(d, []):
            sec_name = sec_info["sector"]
            for stk in sec_info["stocks"]:
                code = stk[0]
                name = stk[1].strip() if len(stk) > 1 else ""
                if code not in stk_map:
                    prefix, mkt_name = get_market(code)
                    stk_map[code] = {
                        "股票代码": code,
                        "股票名称": name,
                        "所属板块": [sec_name],
                        "东财链接": get_eastmoney_url(code),
                        "市场类型": mkt_name
                    }
                else:
                    if sec_name not in stk_map[code]["所属板块"]:
                        stk_map[code]["所属板块"].append(sec_name)
        day_all_stocks[d] = list(stk_map.values())
        if d not in day_total_zts or day_total_zts.get(d, 0) == 0:
            day_total_zts[d] = len(day_all_stocks[d])

    return {
        "trading_dates": trading_dates,
        "date_labels": [[d[0], d[1], d[2]] for d in dates_raw],
        "day_total_zts": day_total_zts,
        "day_emotions": day_emotions,
        "day_emotion_temps": day_emotion_temps,
        "daily_rankings": daily_rankings,
        "block_stocks_data": block_stocks_data,
        "day_all_stocks": day_all_stocks
    }


# =====================================================================
# 整合 15 天数据
# =====================================================================
def get_full_data():
    """整合本地历史 + 线上抓取，输出最近 TARGET_DAYS 天的完整数据"""
    db = load_history()

    # 尝试线上抓取
    try:
        html = fetch_html()
        live = parse_mainline_html(html)
        db = merge_into_history(db, live)
        save_history(db)
        log(f"线上抓取成功，已合并入历史库")
    except Exception as e:
        log(f"线上抓取失败: {e}，将使用本地历史数据继续")

    # 获取最近 TARGET_DAYS 天
    all_dates = sorted(db.get("trading_dates", []), reverse=True)
    target_dates = all_dates[:TARGET_DAYS]
    log(f"截取最近 {len(target_dates)} 天: {target_dates}")

    if not target_dates:
        log("警告: 没有任何历史交易日数据!")
        # 使用 fallback 数据
        target_dates, db = _fallback_data(db)
        target_dates = target_dates[:TARGET_DAYS]

    # 构建 Top10 矩阵
    matrix_top10 = []
    for rank in range(1, 11):
        row = {"序号": rank}
        for d in target_dates:
            sectors = db.get("daily_rankings", {}).get(d, [])
            if rank <= len(sectors):
                sec = sectors[rank - 1]
                row[d] = {"sector": sec["sector"], "count": sec["count"]}
            else:
                row[d] = None
        matrix_top10.append(row)

    # 日期标签
    label_dict = {item[0]: item for item in db.get("date_labels", [])}
    date_labels = []
    for d in target_dates:
        if d in label_dict:
            date_labels.append(label_dict[d])
        else:
            date_labels.append([d, d[5:], get_week_cn(d)])

    return {
        "crawl_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "trading_dates": target_dates,
        "date_labels": date_labels,
        "day_total_zts": {d: db.get("day_total_zts", {}).get(d, 0) for d in target_dates},
        "day_emotions": {d: db.get("day_emotions", {}).get(d, "--") for d in target_dates},
        "day_emotion_temps": {d: db.get("day_emotion_temps", {}).get(d, 0) for d in target_dates},
        "matrix_top10": matrix_top10,
        "daily_rankings": {d: db.get("daily_rankings", {}).get(d, []) for d in target_dates},
        "block_stocks_data": {sec: {d: slist for d, slist in dmap.items() if d in target_dates}
                              for sec, dmap in db.get("block_stocks_data", {}).items()},
        "day_all_stocks": {d: db.get("day_all_stocks", {}).get(d, []) for d in target_dates}
    }


def _fallback_data(db):
    """当无数据时提供 fallback (基于已知历史交易日)"""
    fallback_dates = [
        "2026-09-11", "2026-09-10", "2026-09-09", "2026-09-08", "2026-09-07",
        "2026-09-04", "2026-09-03", "2026-09-02", "2026-09-01", "2026-08-31",
        "2026-08-28", "2026-08-27", "2026-08-26", "2026-08-25", "2026-08-21"
    ]
    fallback_totals = {
        "2026-09-11": 40, "2026-09-10": 35, "2026-09-09": 48, "2026-09-08": 73,
        "2026-09-07": 93, "2026-09-04": 39, "2026-09-03": 44, "2026-09-02": 52,
        "2026-09-01": 83, "2026-08-31": 88, "2026-08-28": 82, "2026-08-27": 77,
        "2026-08-26": 45, "2026-08-25": 61, "2026-08-24": 32, "2026-08-21": 31
    }
    fallback_emotions = {
        "2026-09-11": ("冰点", 14), "2026-09-10": ("冰点", 13),
        "2026-09-09": ("退潮", 38), "2026-09-08": ("降温", 71),
        "2026-09-07": ("高潮", 89), "2026-09-04": ("冰点", 17),
        "2026-09-03": ("退潮", 19), "2026-09-02": ("退潮", 29),
        "2026-09-01": ("升温", 78), "2026-08-31": ("升温", 72),
        "2026-08-28": ("升温", 73), "2026-08-27": ("升温", 71),
        "2026-08-26": ("退潮", 45), "2026-08-25": ("修复", 61),
        "2026-08-24": ("退潮", 32), "2026-08-21": ("退潮", 31)
    }
    for d in fallback_dates:
        if d not in db.get("trading_dates", []):
            db.setdefault("trading_dates", []).append(d)
        if d not in db.get("day_total_zts", {}):
            db["day_total_zts"][d] = fallback_totals.get(d, 0)
        if d not in db.get("day_emotions", {}):
            emo, temp = fallback_emotions.get(d, ("--", 0))
            db.setdefault("day_emotions", {})[d] = emo
            db.setdefault("day_emotion_temps", {})[d] = temp
    save_history(db)
    return fallback_dates, db


# =====================================================================
# HTML 模板 (自包含，无外部依赖)
# =====================================================================
HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>题材热度矩阵 & 涨停走势复盘 (15天)</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inconsolata:wght@400;600;700;800;900&family=Noto+Sans+SC:wght@300;400;500;700;900&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg-base: #0a0e17; --bg-surface: #111827; --bg-card: #162032;
      --border-subtle: rgba(255,255,255,0.08);
      --accent-cyan: #06b6d4; --accent-blue: #38bdf8; --accent-purple: #818cf8;
      --accent-gold: #f59e0b; --stock-up: #ef4444; --stock-up-glow: rgba(239,68,68,0.25);
      --text-primary: #f3f4f6; --text-secondary: #9ca3af; --text-muted: #64748b;
      --highlight-bg: rgba(6,182,212,0.18); --highlight-border: #06b6d4; --highlight-text: #38bdf8;
      --active-bg: rgba(239,68,68,0.25); --active-border: #ef4444;
      --chart-yellow: #facc15; --chart-red-fill: rgba(185,28,28,0.65); --chart-red-stroke: #ef4444;
      --font-mono: 'Inconsolata', Consolas, 'SF Mono', Monaco, monospace;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; -webkit-tap-highlight-color: transparent; }
    body { background-color: var(--bg-base); color: var(--text-primary); font-family: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; min-height: 100vh; line-height: 1.4; overflow-x: hidden; }
    .num-font, .stock-code, .stat-value, .date-header, .rank-col, .pair-code, .count-badge, .tot-zt-cell { font-family: var(--font-mono) !important; font-variant-numeric: slashed-zero tabular-nums !important; letter-spacing: 0.5px; }
    .ambient-glow { position: fixed; top: -120px; left: 50%; transform: translateX(-50%); width: 800px; height: 350px; background: radial-gradient(circle, rgba(6,182,212,0.15) 0%, rgba(129,140,248,0.08) 40%, transparent 70%); pointer-events: none; z-index: 0; }
    .container { max-width: 1560px; margin: 0 auto; padding: 16px 14px 60px; position: relative; z-index: 1; }
    header { margin-bottom: 16px; }
    .header-top { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; padding-bottom: 12px; border-bottom: 1px solid var(--border-subtle); }
    .logo-area { display: flex; align-items: center; gap: 10px; }
    .logo-icon { width: 32px; height: 32px; background: linear-gradient(135deg, #06b6d4, #3b82f6); border-radius: 6px; display: flex; align-items: center; justify-content: center; font-weight: 900; color: #fff; font-size: 16px; box-shadow: 0 0 12px rgba(6,182,212,0.4); }
    .logo-title h1 { font-size: 18px; font-weight: 700; letter-spacing: -0.5px; background: linear-gradient(90deg, #fff, #94a3b8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
    .logo-title p { font-size: 11px; color: var(--text-muted); }
    .badge-tag { font-size: 11px; padding: 3px 8px; border-radius: 999px; background: rgba(255,255,255,0.05); border: 1px solid var(--border-subtle); color: var(--text-secondary); display: flex; align-items: center; gap: 6px; }
    .pulse-dot { width: 6px; height: 6px; background: #10b981; border-radius: 50%; box-shadow: 0 0 8px #10b981; animation: pulse 2s infinite; }
    @keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(0.85); } }
    .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 10px; margin-top: 12px; }
    .stat-card { background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: 10px; padding: 10px 14px; position: relative; overflow: hidden; transition: border-color 0.2s, transform 0.2s; }
    .stat-card:hover { border-color: rgba(255,255,255,0.15); transform: translateY(-1px); }
    .stat-label { font-size: 11px; color: var(--text-secondary); margin-bottom: 2px; }
    .stat-value { font-size: 18px; font-weight: 700; color: #fff; }
    .stat-value.highlight-red { color: var(--stock-up); text-shadow: 0 0 10px var(--stock-up-glow); }
    .stat-value.highlight-cyan { color: var(--accent-cyan); }
    .stat-value.highlight-gold { color: var(--accent-gold); }
    .card { background: var(--bg-surface); border: 1px solid var(--border-subtle); border-radius: 12px; padding: 14px; margin-bottom: 16px; box-shadow: 0 6px 20px rgba(0,0,0,0.35); }
    .card-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
    .card-title { font-size: 15px; font-weight: 700; display: flex; align-items: center; gap: 6px; }
    .card-title .icon-badge { font-size: 12px; padding: 2px 5px; border-radius: 4px; background: rgba(6,182,212,0.15); color: var(--accent-cyan); }
    .card-subtitle { font-size: 11px; color: var(--text-muted); }
    .chart-container { width: 100%; background: #090d16; border: 1px solid var(--border-subtle); border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; padding: 12px 0 0 0; position: relative; overflow-x: auto; -webkit-overflow-scrolling: touch; scrollbar-width: none; }
    .chart-container::-webkit-scrollbar { display: none; }
    .chart-header { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 8px; padding: 0 12px; font-size: 12px; }
    .chart-legend { display: flex; align-items: center; gap: 14px; font-size: 11px; color: var(--text-secondary); flex-wrap: wrap; }
    .legend-item { display: flex; align-items: center; gap: 5px; }
    .legend-line-yellow { width: 14px; height: 2px; background: var(--chart-yellow); display: inline-block; position: relative; }
    .legend-line-yellow::after { content: ''; position: absolute; width: 5px; height: 5px; background: #fff; border: 1px solid var(--chart-yellow); border-radius: 50%; left: 50%; top: 50%; transform: translate(-50%, -50%); }
    .legend-box-red { width: 12px; height: 10px; background: var(--chart-red-fill); border: 1px solid var(--chart-red-stroke); border-radius: 2px; display: inline-block; }
    .legend-line-cyan { width: 14px; height: 2px; background: var(--accent-cyan); display: inline-block; position: relative; }
    .legend-line-cyan::after { content: ''; position: absolute; width: 5px; height: 5px; background: #fff; border: 1px solid var(--accent-cyan); border-radius: 50%; left: 50%; top: 50%; transform: translate(-50%, -50%); }
    .chart-info-tag { font-size: 11px; color: var(--text-primary); display: flex; align-items: center; gap: 8px; background: rgba(255,255,255,0.05); padding: 3px 10px; border-radius: 6px; border: 1px solid var(--border-subtle); flex-wrap: wrap; }
    .chart-svg-wrapper { width: 100%; min-width: 1100px; height: 320px; position: relative; overflow: visible; }
    .chart-svg { width: 100%; height: 100%; overflow: visible; display: block; }
    .matrix-wrapper { width: 100%; overflow-x: auto; border-bottom-left-radius: 8px; border-bottom-right-radius: 8px; border: 1px solid var(--border-subtle); background: #0d131f; -webkit-overflow-scrolling: touch; }
    table.matrix-table { width: 100%; border-collapse: collapse; text-align: center; min-width: 1100px; font-size: 12px; }
    table.matrix-table th, table.matrix-table td { padding: 4px 3px; border: 1px solid rgba(255,255,255,0.04); white-space: nowrap; height: 32px; }
    table.matrix-table th { background: #141c2c; color: var(--text-secondary); font-weight: 600; font-size: 11px; padding: 6px 3px; position: sticky; top: 0; z-index: 2; }
    table.matrix-table th.date-header { color: #cbd5e1; cursor: pointer; transition: all 0.15s; }
    table.matrix-table th.date-header:hover { color: var(--accent-cyan); background: rgba(6,182,212,0.08); }
    table.matrix-table th.date-header.is-latest { color: var(--accent-cyan); background: rgba(6,182,212,0.12); border-bottom: 2px solid var(--accent-cyan); }
    table.matrix-table th.date-header.is-selected-date { background: rgba(56,189,248,0.15); border-bottom: 2px solid var(--accent-blue); color: #fff; }
    table.matrix-table td.rank-col, table.matrix-table th.rank-col { width: 68px; min-width: 68px; background: #141c2c; color: var(--text-muted); font-weight: 700; font-size: 11px; position: sticky; left: 0; z-index: 1; }
    tr.tot-zt-row { background: rgba(239,68,68,0.07); border-bottom: 2px solid rgba(239,68,68,0.3); }
    tr.tot-zt-row td.rank-col { background: #181d2c; color: #fca5a5; font-size: 11px; font-weight: 700; border-right: 1px solid rgba(255,255,255,0.08); }
    .tot-zt-cell { color: #fbbf24; font-weight: 800; font-size: 13px; cursor: pointer; transition: all 0.15s; }
    .tot-zt-cell:hover { color: #fff; background: rgba(251,191,36,0.15); }
    .tot-zt-cell.is-selected-date { color: #f87171; background: rgba(239,68,68,0.18); box-shadow: inset 0 0 6px rgba(239,68,68,0.3); }
    .emotion-row { background: rgba(17,24,39,0.65); }
    .emotion-row td { height: 30px; padding: 3px 2px; border-bottom: 1px solid rgba(255,255,255,0.08); }
    .emotion-cell { cursor: pointer; transition: all 0.15s; }
    .emotion-cell:hover { background: rgba(255,255,255,0.05); }
    .emotion-cell.is-selected-date { background: rgba(6,182,212,0.12) !important; }
    .emotion-tag { display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 600; line-height: 1.3; letter-spacing: 0.5px; white-space: nowrap; }
    .emo-ice { background: rgba(6,182,212,0.18); color: #38bdf8; border: 1px solid rgba(6,182,212,0.4); box-shadow: 0 0 6px rgba(6,182,212,0.15); }
    .emo-fade { background: rgba(100,116,139,0.22); color: #94a3b8; border: 1px solid rgba(148,163,184,0.3); }
    .emo-warm { background: rgba(245,158,11,0.18); color: #fbbf24; border: 1px solid rgba(245,158,11,0.4); }
    .emo-hot { background: rgba(239,68,68,0.22); color: #f87171; border: 1px solid rgba(239,68,68,0.45); box-shadow: 0 0 8px rgba(239,68,68,0.2); }
    .rank-badge { display: inline-block; width: 18px; height: 18px; line-height: 18px; border-radius: 3px; background: rgba(255,255,255,0.05); }
    .rank-1 .rank-badge { background: #ef4444; color: #fff; font-weight: 700; }
    .rank-2 .rank-badge { background: #f97316; color: #fff; }
    .rank-3 .rank-badge { background: #f59e0b; color: #fff; }
    .block-cell { cursor: pointer; padding: 2px 4px; border-radius: 4px; transition: all 0.12s ease; user-select: none; display: flex; align-items: center; justify-content: center; gap: 3px; color: #e2e8f0; font-weight: 500; font-size: 11px; line-height: 1.2; }
    .block-cell:hover { background: rgba(255,255,255,0.08); color: #fff; }
    .block-cell.empty { cursor: default; color: var(--text-muted); background: transparent !important; }
    .block-cell.highlight-same { background: var(--highlight-bg) !important; border: 1px solid var(--highlight-border) !important; color: var(--highlight-text) !important; font-weight: 700; box-shadow: 0 0 8px rgba(6,182,212,0.35); }
    .block-cell.active-selected { background: var(--active-bg) !important; border: 1px solid var(--active-border) !important; color: #fff !important; font-weight: 700; box-shadow: 0 0 10px rgba(239,68,68,0.45); transform: scale(1.02); }
    .search-box { position: relative; max-width: 260px; width: 100%; }
    .search-box input { width: 100%; background: #0d131f; border: 1px solid var(--border-subtle); color: #fff; padding: 6px 10px 6px 28px; border-radius: 6px; font-size: 12px; outline: none; transition: border-color 0.2s; }
    .search-box input:focus { border-color: var(--accent-cyan); }
    .search-box svg { position: absolute; left: 8px; top: 50%; transform: translateY(-50%); width: 13px; height: 13px; fill: var(--text-muted); }
    a.stock-link { text-decoration: none; transition: all 0.15s ease; display: inline-flex; align-items: center; gap: 2px; }
    a.stock-link:hover { text-decoration: underline; opacity: 0.9; }
    .stock-code-link { color: var(--accent-cyan) !important; font-weight: 700; font-size: 13px; }
    .stock-code-link:hover { color: #38bdf8 !important; text-shadow: 0 0 8px rgba(6,182,212,0.5); }
    .stock-name-link { color: #f3f4f6 !important; font-weight: 600; }
    .stock-name-link:hover { color: #facc15 !important; text-shadow: 0 0 8px rgba(250,204,21,0.4); }
    .detail-table-wrapper { width: 100%; overflow-x: auto; border-radius: 8px; border: 1px solid var(--border-subtle); background: #0d131f; -webkit-overflow-scrolling: touch; max-height: 480px; overflow-y: auto; }
    table.detail-table { width: 100%; border-collapse: collapse; text-align: left; font-size: 12px; min-width: 600px; }
    table.detail-table th, table.detail-table td { padding: 8px 12px; border-bottom: 1px solid rgba(255,255,255,0.04); white-space: nowrap; }
    table.detail-table th { background: #141c2c; color: var(--text-secondary); font-size: 11px; font-weight: 600; position: sticky; top: 0; cursor: pointer; user-select: none; z-index: 2; }
    table.detail-table th:hover { color: #fff; }
    table.detail-table tbody tr { transition: background-color 0.12s; }
    table.detail-table tbody tr:hover { background-color: rgba(255,255,255,0.05); }
    .em-tag { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; padding: 2px 6px; border-radius: 4px; background: rgba(56,189,248,0.12); border: 1px solid rgba(56,189,248,0.25); color: #38bdf8; text-decoration: none; transition: all 0.15s; }
    .em-tag:hover { background: rgba(56,189,248,0.25); color: #fff; }
    .sector-badge { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; background: rgba(129,140,248,0.15); color: #a5b4fc; border: 1px solid rgba(129,140,248,0.3); margin-right: 4px; margin-bottom: 2px; }
    .summary-grid-wrapper { width: 100%; overflow-x: auto; border-radius: 8px; border: 1px solid var(--border-subtle); background: #0d131f; -webkit-overflow-scrolling: touch; }
    table.summary-multi-table { width: 100%; border-collapse: collapse; text-align: left; font-size: 12px; }
    table.summary-multi-table th, table.summary-multi-table td { padding: 6px 10px; border-bottom: 1px solid rgba(255,255,255,0.04); white-space: nowrap; height: 30px; }
    table.summary-multi-table th { background: #141c2c; color: var(--text-secondary); font-size: 11px; font-weight: 600; position: sticky; top: 0; z-index: 2; }
    table.summary-multi-table tbody tr { transition: background-color 0.12s; }
    table.summary-multi-table tbody tr:hover { background-color: rgba(255,255,255,0.05); }
    .pair-code { text-align: center !important; width: 90px; }
    .pair-name { text-align: left !important; padding-right: 14px !important; }
    .pair-border { border-right: 1px solid rgba(255,255,255,0.1) !important; }
    .empty-state { padding: 30px; text-align: center; color: var(--text-muted); font-size: 13px; }
    @media (max-width: 768px) {
      .container { padding: 10px 8px 40px; }
      .logo-title h1 { font-size: 16px; }
      .stats-grid { grid-template-columns: repeat(2, 1fr); gap: 6px; }
      .stat-card { padding: 8px 10px; }
      .stat-value { font-size: 15px; }
      .card { padding: 10px 8px; margin-bottom: 12px; }
      .chart-svg-wrapper { height: 260px; }
    }
  </style>
</head>
<body>
  <div class="ambient-glow"></div>
  <div class="container">
    <header>
      <div class="header-top">
        <div class="logo-area">
          <div class="logo-icon">梯</div>
          <div class="logo-title">
            <h1>题材热度矩阵 · 龙头板块复盘</h1>
            <p>游资主线题材热度矩阵 (近15个交易日)</p>
          </div>
        </div>
        <div class="header-actions">
          <div class="badge-tag">
            <span class="pulse-dot"></span>
            <span id="update-time-tag">数据已就绪 (15天)</span>
          </div>
        </div>
      </div>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">最新交易日</div>
          <div class="stat-value highlight-cyan" id="stat-latest-date">--</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">当前选中板块</div>
          <div class="stat-value" id="stat-selected-block">--</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">选中板块关联股</div>
          <div class="stat-value highlight-red" id="stat-stock-count">0 只</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">当日全部题材股汇总</div>
          <div class="stat-value highlight-gold" id="stat-day-total-count">0 只</div>
        </div>
      </div>
    </header>

    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">
            <span class="icon-badge">TOP</span>
            题材热度矩阵 (近15个交易日龙头板块排名与涨停走势)
          </div>
          <div class="card-subtitle">黄色线=沪深涨停总数 | 红色阴影=选中板块涨停数 | 青色线=情绪温度(右轴) | 点击板块联动高亮同名板块</div>
        </div>
      </div>

      <div class="chart-container">
        <div class="chart-header">
          <div style="font-weight: 700; color: #f87171; display: flex; align-items: center; gap: 6px;">
            <span style="display:inline-block;width:3px;height:12px;background:#ef4444;border-radius:2px;"></span>
            <span>涨停棱镜 (近15天)</span>
          </div>
          <div class="chart-legend">
            <div class="legend-item"><span class="legend-line-yellow"></span><span>沪深涨停</span></div>
            <div class="legend-item"><span class="legend-box-red"></span><span>主题涨停</span></div>
            <div class="legend-item"><span class="legend-line-cyan"></span><span>情绪温度</span></div>
            <div class="chart-info-tag" id="chart-status-info">
              <span>日期: <b id="chart-info-date" class="num-font" style="color:var(--accent-cyan);">--</b></span>
              <span>情绪: <b id="chart-info-emo" style="color:#38bdf8;">--</b></span>
              <span>温度: <b id="chart-info-temp" class="num-font" style="color:var(--accent-cyan);">--</b></span>
              <span>沪深涨停: <b id="chart-info-tot" class="num-font" style="color:#fbbf24;">--</b></span>
              <span><span id="chart-info-sec-name">选中板块</span>: <b id="chart-info-sec-cnt" class="num-font" style="color:#f87171;">--</b></span>
            </div>
          </div>
        </div>
        <div class="chart-svg-wrapper" id="chart-svg-wrapper">
          <svg class="chart-svg" id="mainline-chart-svg"></svg>
        </div>
      </div>

      <div class="matrix-wrapper">
        <table class="matrix-table" id="matrix-table">
          <thead><tr id="matrix-header-row"></tr></thead>
          <tbody id="matrix-body"></tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">
            <span class="icon-badge">BLOCK</span>
            <span id="detail-table-title">选中板块联动个股明细</span>
          </div>
          <div class="card-subtitle">展示选中板块在指定交易日的涨停个股清单（点击代码或名称跳转东财概念行情）</div>
        </div>
        <div class="search-box">
          <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
          <input type="text" id="stock-search-input" placeholder="在当前板块中搜索股票代码/名称..." />
        </div>
      </div>
      <div class="detail-table-wrapper">
        <table class="detail-table" id="detail-table">
          <thead>
            <tr>
              <th onclick="sortBlockTable('序号')">序号</th>
              <th onclick="sortBlockTable('股票代码')">股票代码</th>
              <th onclick="sortBlockTable('股票名称')">股票名称</th>
              <th onclick="sortBlockTable('板块名称')">所属板块</th>
              <th onclick="sortBlockTable('市场类型')">市场类型</th>
              <th>行情链接</th>
            </tr>
          </thead>
          <tbody id="detail-table-body"></tbody>
        </table>
      </div>
    </section>

    <section class="card">
      <div class="card-header">
        <div>
          <div class="card-title">
            <span class="icon-badge">ALL</span>
            <span id="all-day-title">当日全部热门题材关联个股汇总</span>
          </div>
          <div class="card-subtitle">汇总当前交易日所有热门入围板块的股票（多列紧凑平铺展示，点击直达东财）</div>
        </div>
        <div class="search-box">
          <svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/></svg>
          <input type="text" id="all-day-search-input" placeholder="在当日全部个股中搜索代码/名称..." />
        </div>
      </div>
      <div class="summary-grid-wrapper">
        <table class="summary-multi-table" id="all-day-multi-table">
          <thead id="all-day-table-head"></thead>
          <tbody id="all-day-table-body"></tbody>
        </table>
      </div>
    </section>
  </div>

  <script>
    const RAW_DATA = __RAW_DATA_PLACEHOLDER__;

    function getEastMoneyUrl(code) {
      code = String(code).trim();
      let prefix = 'sh';
      if (code.startsWith('60') || code.startsWith('688') || code.startsWith('689') || code.startsWith('900')) prefix = 'sh';
      else if (code.startsWith('00') || code.startsWith('30') || code.startsWith('200')) prefix = 'sz';
      else if (code.startsWith('8') || code.startsWith('4') || code.startsWith('920')) prefix = 'bj';
      else prefix = parseInt(code.substring(0,3)) >= 600 ? 'sh' : 'sz';
      return `https://quote.eastmoney.com/concept/${prefix}${code}.html`;
    }

    const state = {
      tradingDates: RAW_DATA.trading_dates || [],
      dateLabels: RAW_DATA.date_labels || [],
      dayTotalZts: RAW_DATA.day_total_zts || {},
      dayEmotions: RAW_DATA.day_emotions || {},
      dayEmotionTemps: RAW_DATA.day_emotion_temps || {},
      selectedDate: (RAW_DATA.trading_dates && RAW_DATA.trading_dates[0]) || "",
      selectedBlockName: "",
      highlightBlockName: "",
      blockStocks: [],
      currentDayStocks: [],
      blockSearchKeyword: "",
      allDaySearchKeyword: "",
      blockSortField: "序号",
      blockSortAsc: true
    };

    document.addEventListener("DOMContentLoaded", () => {
      initData();
      initEventListeners();
      renderMatrixTable();
      updateSelectedContext(state.selectedDate, state.selectedBlockName);
      setTimeout(() => renderPrismChart(), 50);
    });

    function initData() {
      const latestDate = state.tradingDates[0] || "--";
      document.getElementById("stat-latest-date").textContent = latestDate;
      state.selectedDate = latestDate;
      const top1 = RAW_DATA.matrix_top10.find(r => r["序号"] === 1);
      if (top1 && top1[latestDate]) {
        state.selectedBlockName = top1[latestDate].sector;
        state.highlightBlockName = top1[latestDate].sector;
      }
    }

    function initEventListeners() {
      const bs = document.getElementById("stock-search-input");
      if (bs) bs.addEventListener("input", e => { state.blockSearchKeyword = e.target.value.trim().toLowerCase(); renderBlockDetailTable(); });
      const ads = document.getElementById("all-day-search-input");
      if (ads) ads.addEventListener("input", e => { state.allDaySearchKeyword = e.target.value.trim().toLowerCase(); renderAllDayMultiTable(); });
      window.addEventListener("resize", () => { renderAllDayMultiTable(); renderPrismChart(); });
      const mw = document.querySelector(".matrix-wrapper");
      const cc = document.querySelector(".chart-container");
      if (mw && cc) {
        mw.addEventListener("scroll", () => { cc.scrollLeft = mw.scrollLeft; });
        cc.addEventListener("scroll", () => { mw.scrollLeft = cc.scrollLeft; });
      }
    }

    function renderMatrixTable() {
      const headerRow = document.getElementById("matrix-header-row");
      const tbody = document.getElementById("matrix-body");
      headerRow.innerHTML = ""; tbody.innerHTML = "";

      const thRank = document.createElement("th");
      thRank.className = "rank-col"; thRank.textContent = "排名";
      headerRow.appendChild(thRank);

      state.tradingDates.forEach((d, idx) => {
        const th = document.createElement("th");
        th.className = "date-header" + (idx === 0 ? " is-latest" : "");
        th.setAttribute("data-date", d);
        if (d === state.selectedDate) th.classList.add("is-selected-date");
        const lo = state.dateLabels.find(l => l[0] === d);
        const dl = lo ? lo[1] : d.substring(5);
        const wl = lo ? lo[2] : "";
        th.innerHTML = `${dl}<br><span style="font-size:10px;font-weight:normal;opacity:0.75;">${wl}</span>`;
        th.title = `点击切换到交易日 ${d}`;
        th.onclick = () => selectDate(d);
        headerRow.appendChild(th);
      });

      // 当日涨停行
      const trTot = document.createElement("tr");
      trTot.className = "tot-zt-row";
      const tdTotL = document.createElement("td");
      tdTotL.className = "rank-col"; tdTotL.textContent = "当日涨停";
      tdTotL.title = "全市场涨停数 (含未封ST及多板块重合)";
      trTot.appendChild(tdTotL);
      state.tradingDates.forEach(d => {
        const td = document.createElement("td");
        const tc = state.dayTotalZts[d] || 0;
        td.className = "tot-zt-cell" + (d === state.selectedDate ? " is-selected-date" : "");
        td.setAttribute("data-date", d);
        td.innerHTML = `<span>${tc}</span>`;
        td.title = `${d} 全市场涨停: ${tc} 只`;
        td.onclick = () => selectDate(d);
        trTot.appendChild(td);
      });
      tbody.appendChild(trTot);

      // 情绪周期行 (在涨停行下方)
      const trEmo = document.createElement("tr");
      trEmo.className = "emotion-row";
      const tdEmoL = document.createElement("td");
      tdEmoL.className = "rank-col";
      tdEmoL.innerHTML = `<span style="color:var(--accent-blue);font-weight:700;">情绪</span>`;
      tdEmoL.title = "市场情绪周期状态与温度";
      trEmo.appendChild(tdEmoL);
      state.tradingDates.forEach(d => {
        const td = document.createElement("td");
        const emo = state.dayEmotions[d] || "--";
        const temp = state.dayEmotionTemps[d] !== undefined ? state.dayEmotionTemps[d] : null;
        td.className = "emotion-cell" + (d === state.selectedDate ? " is-selected-date" : "");
        td.setAttribute("data-date", d);
        let ec = "emotion-tag";
        if (emo.includes("冰点")) ec += " emo-ice";
        else if (emo.includes("退潮") || emo.includes("降温")) ec += " emo-fade";
        else if (emo.includes("升温") || emo.includes("修复")) ec += " emo-warm";
        else if (emo.includes("高潮") || emo.includes("沸点")) ec += " emo-hot";
        else ec += " emo-fade";
        const tt = (temp !== null && temp !== undefined) ? `${temp}°` : "";
        td.innerHTML = `<span class="${ec}" title="${d} 情绪: ${emo}${tt ? ' · ' + tt : ''}">${emo}${tt ? ' ' + tt : ''}</span>`;
        td.onclick = () => selectDate(d);
        trEmo.appendChild(td);
      });
      tbody.appendChild(trEmo);

      // Top10 板块行
      RAW_DATA.matrix_top10.forEach(row => {
        const rank = row["序号"];
        const tr = document.createElement("tr");
        if (rank <= 3) tr.className = `rank-${rank}`;
        const tdR = document.createElement("td");
        tdR.className = "rank-col";
        tdR.innerHTML = `<span class="rank-badge">${rank}</span>`;
        tr.appendChild(tdR);
        state.tradingDates.forEach(d => {
          const td = document.createElement("td");
          const bi = row[d];
          if (!bi || !bi.sector) {
            td.innerHTML = `<div class="block-cell empty">-</div>`;
          } else {
            const sn = bi.sector, cnt = bi.count || 0;
            const isSame = state.highlightBlockName && sn === state.highlightBlockName;
            const isActive = d === state.selectedDate && sn === state.selectedBlockName;
            let cc = "block-cell";
            if (isSame) cc += " highlight-same";
            if (isActive) cc += " active-selected";
            td.innerHTML = `<div class="${cc}" data-date="${d}" data-sector="${sn}" title="${sn} (${d} · ${cnt}只涨停)"><span>${sn}</span></div>`;
            td.onclick = () => onBlockClick(d, sn);
          }
          tr.appendChild(td);
        });
        tbody.appendChild(tr);
      });
    }

    function renderPrismChart() {
      const svg = document.getElementById("mainline-chart-svg");
      const wrapper = document.getElementById("chart-svg-wrapper");
      const mt = document.getElementById("matrix-table");
      if (!svg || !wrapper || !mt) return;
      const dateThs = document.querySelectorAll(".matrix-table th.date-header");
      if (!dateThs || dateThs.length === 0) return;

      const tw = mt.offsetWidth;
      wrapper.style.width = `${tw}px`;
      wrapper.style.minWidth = `${tw}px`;
      const width = tw, height = 320;
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.innerHTML = "";

      const totList = state.tradingDates.map(d => state.dayTotalZts[d] || 0);
      const curSec = state.selectedBlockName || "";
      const secList = state.tradingDates.map(d => {
        if (!curSec || !RAW_DATA.block_stocks_data[curSec] || !RAW_DATA.block_stocks_data[curSec][d]) return 0;
        return RAW_DATA.block_stocks_data[curSec][d].length;
      });

      const maxTot = Math.max(...totList, 5);
      const yMax = Math.ceil(maxTot / 5) * 5;

      const svgRect = svg.getBoundingClientRect();
      const xCoords = [];
      dateThs.forEach(th => {
        const r = th.getBoundingClientRect();
        xCoords.push((r.left + r.width / 2) - svgRect.left);
      });
      if (xCoords.length < state.tradingDates.length) return;

      const tp = 24, bp = 24, ph = height - tp - bp;
      const getY = v => tp + ph * (1 - v / yMax);
      const getYT = v => tp + ph * (1 - v / 100);

      let tickStep = 20;
      if (yMax <= 25) tickStep = 5;
      else if (yMax <= 60) tickStep = 10;
      else if (yMax <= 120) tickStep = 20;
      else if (yMax <= 250) tickStep = 50;
      else tickStep = 100;

      // 左轴刻度线
      const yTicks = [];
      for (let v = tickStep; v < yMax; v += tickStep) {
        if (yMax - v >= tickStep * 0.4) yTicks.push({ val: v, label: String(v) });
      }
      yTicks.push({ val: yMax, label: String(yMax) });

      yTicks.forEach(tick => {
        const y = getY(tick.val);
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", "10"); line.setAttribute("y1", y);
        line.setAttribute("x2", width - 10); line.setAttribute("y2", y);
        line.setAttribute("stroke", "rgba(239,68,68,0.15)");
        line.setAttribute("stroke-dasharray", "3,3");
        svg.appendChild(line);
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", "12"); text.setAttribute("y", y - 4);
        text.setAttribute("fill", "rgba(255,255,255,0.35)");
        text.setAttribute("font-size", "9px");
        text.setAttribute("font-family", "var(--font-mono)");
        text.textContent = tick.label;
        svg.appendChild(text);
      });

      // 右轴情绪温度刻度
      [0, 20, 40, 60, 80, 100].forEach(tVal => {
        const yT = getYT(tVal);
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("x", width - 12); text.setAttribute("y", yT - 4);
        text.setAttribute("text-anchor", "end");
        text.setAttribute("fill", "rgba(6,182,212,0.7)");
        text.setAttribute("font-size", "9px");
        text.setAttribute("font-family", "var(--font-mono)");
        text.textContent = `${tVal}\u00b0`;
        svg.appendChild(text);
      });

      // 轴标题
      const lt = document.createElementNS("http://www.w3.org/2000/svg", "text");
      lt.setAttribute("x", "12"); lt.setAttribute("y", tp - 10);
      lt.setAttribute("fill", "rgba(251,191,36,0.75)");
      lt.setAttribute("font-size", "9px");
      lt.setAttribute("font-family", "var(--font-mono)");
      lt.textContent = "涨停数";
      svg.appendChild(lt);

      const rt = document.createElementNS("http://www.w3.org/2000/svg", "text");
      rt.setAttribute("x", width - 12); rt.setAttribute("y", tp - 10);
      rt.setAttribute("text-anchor", "end");
      rt.setAttribute("fill", "rgba(6,182,212,0.75)");
      rt.setAttribute("font-size", "9px");
      rt.setAttribute("font-family", "var(--font-mono)");
      rt.textContent = "情绪温度";
      svg.appendChild(rt);

      // 垂直参考线
      state.tradingDates.forEach((d, i) => {
        const x = xCoords[i];
        const yT = getY(totList[i]);
        const yB = getY(0);
        const vl = document.createElementNS("http://www.w3.org/2000/svg", "line");
        vl.setAttribute("x1", x); vl.setAttribute("y1", yT);
        vl.setAttribute("x2", x); vl.setAttribute("y2", yB);
        vl.setAttribute("stroke", d === state.selectedDate ? "rgba(56,189,248,0.65)" : "rgba(56,189,248,0.22)");
        vl.setAttribute("stroke-dasharray", "3,3");
        svg.appendChild(vl);
      });

      // 红色阴影区域 (选中板块涨停)
      let redD = `M ${xCoords[0]} ${getY(0)}`;
      xCoords.forEach((x, i) => { redD += ` L ${x} ${getY(secList[i])}`; });
      redD += ` L ${xCoords[xCoords.length-1]} ${getY(0)} Z`;
      const rp = document.createElementNS("http://www.w3.org/2000/svg", "path");
      rp.setAttribute("d", redD);
      rp.setAttribute("fill", "var(--chart-red-fill)");
      rp.setAttribute("stroke", "var(--chart-red-stroke)");
      rp.setAttribute("stroke-width", "1.5");
      rp.setAttribute("opacity", "0.9");
      svg.appendChild(rp);

      // 黄色折线 (全市场涨停)
      let yD = "";
      xCoords.forEach((x, i) => { const y = getY(totList[i]); yD += (i === 0 ? "M " : " L ") + x + " " + y; });
      const yp = document.createElementNS("http://www.w3.org/2000/svg", "path");
      yp.setAttribute("d", yD);
      yp.setAttribute("fill", "none");
      yp.setAttribute("stroke", "var(--chart-yellow)");
      yp.setAttribute("stroke-width", "2");
      yp.setAttribute("stroke-linejoin", "round");
      yp.setAttribute("stroke-linecap", "round");
      svg.appendChild(yp);

      // 黄色数据点
      state.tradingDates.forEach((d, i) => {
        const x = xCoords[i], y = getY(totList[i]);
        const sel = d === state.selectedDate;
        if (sel) {
          const gc = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          gc.setAttribute("cx", x); gc.setAttribute("cy", y); gc.setAttribute("r", "7.5");
          gc.setAttribute("fill", "rgba(250,204,21,0.35)");
          gc.setAttribute("stroke", "var(--chart-yellow)");
          gc.setAttribute("stroke-width", "2");
          svg.appendChild(gc);
        }
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("cx", x); dot.setAttribute("cy", y);
        dot.setAttribute("r", sel ? "4" : "2.8");
        dot.setAttribute("fill", sel ? "#fff" : "var(--chart-yellow)");
        dot.setAttribute("stroke", "#090d16");
        dot.setAttribute("stroke-width", "1");
        dot.style.cursor = "pointer";
        dot.onclick = () => selectDate(d);
        svg.appendChild(dot);
      });

      // 青色情绪温度曲线 (右轴)
      const tempList = state.tradingDates.map(d => state.dayEmotionTemps[d] !== undefined ? state.dayEmotionTemps[d] : 0);
      let cD = "";
      xCoords.forEach((x, i) => { const y = getYT(tempList[i]); cD += (i === 0 ? "M " : " L ") + x + " " + y; });
      const cp = document.createElementNS("http://www.w3.org/2000/svg", "path");
      cp.setAttribute("d", cD);
      cp.setAttribute("fill", "none");
      cp.setAttribute("stroke", "var(--accent-cyan)");
      cp.setAttribute("stroke-width", "2");
      cp.setAttribute("stroke-linejoin", "round");
      cp.setAttribute("stroke-linecap", "round");
      cp.setAttribute("opacity", "0.95");
      svg.appendChild(cp);

      // 温度数据点
      state.tradingDates.forEach((d, i) => {
        const x = xCoords[i], y = getYT(tempList[i]);
        const sel = d === state.selectedDate;
        const emo = state.dayEmotions[d] || "--";
        if (sel) {
          const gc = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          gc.setAttribute("cx", x); gc.setAttribute("cy", y); gc.setAttribute("r", "7");
          gc.setAttribute("fill", "rgba(6,182,212,0.35)");
          gc.setAttribute("stroke", "var(--accent-cyan)");
          gc.setAttribute("stroke-width", "1.5");
          svg.appendChild(gc);
        }
        const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        dot.setAttribute("cx", x); dot.setAttribute("cy", y);
        dot.setAttribute("r", sel ? "3.8" : "2.5");
        dot.setAttribute("fill", sel ? "#fff" : "var(--accent-cyan)");
        dot.setAttribute("stroke", "#090d16");
        dot.setAttribute("stroke-width", "1");
        dot.style.cursor = "pointer";
        const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
        title.textContent = `${d} 情绪: ${emo} · 温度: ${tempList[i]}\u00b0`;
        dot.appendChild(title);
        dot.onclick = () => selectDate(d);
        svg.appendChild(dot);
      });

      // 更新信息标签
      const sd = state.selectedDate;
      const ct = state.dayTotalZts[sd] || 0;
      const cc2 = (curSec && RAW_DATA.block_stocks_data[curSec] && RAW_DATA.block_stocks_data[curSec][sd]) ? RAW_DATA.block_stocks_data[curSec][sd].length : 0;
      const ce = state.dayEmotions[sd] || "--";
      const ctp = state.dayEmotionTemps[sd] !== undefined ? `${state.dayEmotionTemps[sd]}\u00b0` : "--\u00b0";
      document.getElementById("chart-info-date").textContent = sd;
      const e1 = document.getElementById("chart-info-emo"); if (e1) e1.textContent = ce;
      const e2 = document.getElementById("chart-info-temp"); if (e2) e2.textContent = ctp;
      document.getElementById("chart-info-tot").textContent = ct;
      document.getElementById("chart-info-sec-name").textContent = curSec || "板块";
      document.getElementById("chart-info-sec-cnt").textContent = curSec ? cc2 : "0";
    }

    function onBlockClick(date, sector) {
      state.selectedDate = date;
      state.selectedBlockName = sector;
      state.highlightBlockName = sector;
      updateSelectedContext(date, sector);
      renderMatrixTable();
      renderPrismChart();
    }

    function selectDate(date) {
      state.selectedDate = date;
      const cs = state.selectedBlockName;
      let has = cs && RAW_DATA.block_stocks_data[cs] && RAW_DATA.block_stocks_data[cs][date] && RAW_DATA.block_stocks_data[cs][date].length > 0;
      if (!has) {
        const r1 = RAW_DATA.matrix_top10.find(r => r["序号"] === 1);
        if (r1 && r1[date]) {
          state.selectedBlockName = r1[date].sector;
          state.highlightBlockName = r1[date].sector;
        }
      }
      updateSelectedContext(state.selectedDate, state.selectedBlockName);
      renderMatrixTable();
      renderPrismChart();
    }

    function updateSelectedContext(date, sector) {
      document.getElementById("stat-selected-block").textContent = sector || "--";
      let stocks = [];
      if (sector && RAW_DATA.block_stocks_data[sector] && RAW_DATA.block_stocks_data[sector][date]) {
        stocks = RAW_DATA.block_stocks_data[sector][date];
      }
      state.blockStocks = stocks;
      document.getElementById("stat-stock-count").textContent = `${stocks.length} 只`;
      let allList = [];
      if (date && RAW_DATA.day_all_stocks[date]) allList = RAW_DATA.day_all_stocks[date];
      state.currentDayStocks = allList;
      document.getElementById("stat-day-total-count").textContent = `${allList.length} 只`;
      document.getElementById("detail-table-title").textContent = `【${sector || "--"}】联动涨停个股明细 (${date} · 共 ${stocks.length} 只)`;
      document.getElementById("all-day-title").textContent = `当日全部热门题材关联个股汇总 (${date} · 共 ${allList.length} 只)`;
      const ce = state.dayEmotions[date] || "--";
      const ctp = state.dayEmotionTemps[date] !== undefined ? `${state.dayEmotionTemps[date]}\u00b0` : "--\u00b0";
      const e1 = document.getElementById("chart-info-emo"); if (e1) e1.textContent = ce;
      const e2 = document.getElementById("chart-info-temp"); if (e2) e2.textContent = ctp;
      renderBlockDetailTable();
      renderAllDayMultiTable();
    }

    function renderBlockDetailTable() {
      const tbody = document.getElementById("detail-table-body");
      tbody.innerHTML = "";
      let list = [...state.blockStocks];
      if (state.blockSearchKeyword) {
        list = list.filter(s => {
          const c = (s["股票代码"] || "").toLowerCase();
          const n = (s["股票名称"] || "").toLowerCase();
          return c.includes(state.blockSearchKeyword) || n.includes(state.blockSearchKeyword);
        });
      }
      if (state.blockSortField) {
        list.sort((a, b) => {
          let va = a[state.blockSortField], vb = b[state.blockSortField];
          if (typeof va === "number" && typeof vb === "number") return state.blockSortAsc ? va - vb : vb - va;
          va = String(va || ""); vb = String(vb || "");
          return state.blockSortAsc ? va.localeCompare(vb, "zh-CN") : vb.localeCompare(va, "zh-CN");
        });
      }
      if (list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="6" class="empty-state">当前板块在选定日期暂无符合条件的个股数据</td></tr>`;
        return;
      }
      list.forEach((s, idx) => {
        const tr = document.createElement("tr");
        const code = s["股票代码"], name = s["股票名称"];
        const url = s["东财链接"] || getEastMoneyUrl(code);
        tr.innerHTML = `
          <td style="color:var(--text-muted);font-family:var(--font-mono);">${idx + 1}</td>
          <td><a href="${url}" target="_blank" rel="noopener" class="stock-link stock-code-link">${code}</a></td>
          <td><a href="${url}" target="_blank" rel="noopener" class="stock-link stock-name-link">${name}</a></td>
          <td><span class="sector-badge">${s["板块名称"] || state.selectedBlockName}</span></td>
          <td><span style="font-size:11px;color:var(--text-secondary);">${s["市场类型"] || "--"}</span></td>
          <td><a href="${url}" target="_blank" rel="noopener" class="em-tag" title="东财概念行情"><span>东财行情</span><svg style="width:10px;height:10px;fill:currentColor;" viewBox="0 0 24 24"><path d="M19 19H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z"/></svg></a></td>`;
        tbody.appendChild(tr);
      });
    }

    function renderAllDayMultiTable() {
      const thead = document.getElementById("all-day-table-head");
      const tbody = document.getElementById("all-day-table-body");
      tbody.innerHTML = "";
      let list = [...state.currentDayStocks];
      if (state.allDaySearchKeyword) {
        list = list.filter(s => {
          const c = (s["股票代码"] || "").toLowerCase();
          const n = (s["股票名称"] || "").toLowerCase();
          return c.includes(state.allDaySearchKeyword) || n.includes(state.allDaySearchKeyword);
        });
      }
      let np = 4;
      const w = window.innerWidth;
      if (w < 640) np = 2;
      else if (w < 1024) np = 3;
      else if (w >= 1360) np = 5;
      let hh = "<tr>";
      for (let p = 0; p < np; p++) {
        const ib = (p < np - 1) ? "pair-border" : "";
        hh += `<th class="pair-code">股票代码</th><th class="pair-name ${ib}">股票名称</th>`;
      }
      hh += "</tr>";
      thead.innerHTML = hh;
      if (list.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${np * 2}" class="empty-state">当日暂无符合条件的个股</td></tr>`;
        return;
      }
      const nr = Math.ceil(list.length / np);
      for (let r = 0; r < nr; r++) {
        const tr = document.createElement("tr");
        let rh = "";
        for (let p = 0; p < np; p++) {
          const idx = r * np + p;
          const ib = (p < np - 1) ? "pair-border" : "";
          if (idx < list.length) {
            const s = list[idx];
            const code = s["股票代码"], name = s["股票名称"];
            const url = s["东财链接"] || getEastMoneyUrl(code);
            rh += `<td class="pair-code"><a href="${url}" target="_blank" rel="noopener" class="stock-link stock-code-link">${code}</a></td><td class="pair-name ${ib}"><a href="${url}" target="_blank" rel="noopener" class="stock-link stock-name-link">${name}</a></td>`;
          } else {
            rh += `<td class="pair-code" style="color:var(--text-muted);">-</td><td class="pair-name ${ib}" style="color:var(--text-muted);">-</td>`;
          }
        }
        tr.innerHTML = rh;
        tbody.appendChild(tr);
      }
    }

    function sortBlockTable(field) {
      if (state.blockSortField === field) state.blockSortAsc = !state.blockSortAsc;
      else { state.blockSortField = field; state.blockSortAsc = true; }
      renderBlockDetailTable();
    }
  </script>
</body>
</html>'''


# =====================================================================
# HTML 生成
# =====================================================================
def generate_html(data, output_filepath):
    """将数据注入模板，生成自包含 HTML 文件"""
    json_str = json.dumps(data, ensure_ascii=False)
    html = HTML_TEMPLATE.replace("__RAW_DATA_PLACEHOLDER__", json_str)
    os.makedirs(os.path.dirname(output_filepath) or ".", exist_ok=True)
    with open(output_filepath, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"已生成: {output_filepath} ({len(html)} 字节)")


# =====================================================================
# 主任务
# =====================================================================
def execute_task(output_dir=OUTPUT_DIR):
    """执行一次完整的抓取+合并+生成任务"""
    log("=" * 60)
    log("开始执行题材热度矩阵每日复盘任务")
    log("=" * 60)
    try:
        data = get_full_data()

        latest_date = data["trading_dates"][0] if data["trading_dates"] else datetime.datetime.now().strftime("%Y-%m-%d")
        date_str = latest_date.replace("-", "")
        filename = f"{date_str}.html"
        output_file = os.path.join(output_dir, filename)

        generate_html(data, output_file)

        log(f"任务完成! 已生成 {filename} (含 {len(data['trading_dates'])} 个交易日)")
        return True
    except Exception as e:
        log(f"[ERROR] 任务失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_schedule_loop(target_time="19:30", output_dir=OUTPUT_DIR):
    """守护进程: 每天在指定时间自动触发执行"""
    log(f"定时调度已启动! 每日【{target_time}】自动执行 (Ctrl+C 停止)")
    th, tm = map(int, target_time.split(":"))
    last_run = None
    while True:
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        if now.hour == th and now.minute >= tm and last_run != today:
            log(f"触发时间到达 ({now.strftime('%H:%M:%S')})，开始执行...")
            if execute_task(output_dir):
                last_run = today
                log(f"今日 ({today}) 任务完成，下次: 明日 {target_time}")
            else:
                log("任务失败，5 分钟后重试...")
                time.sleep(300)
                continue
        time.sleep(20)


def main():
    parser = argparse.ArgumentParser(description="A股主线题材热度矩阵每日生成器 (15天)")
    parser.add_argument("--now", "-n", action="store_true", help="立即执行一次并退出")
    parser.add_argument("--time", "-t", type=str, default=DEFAULT_RUN_TIME, help=f"每日执行时间 HH:MM (默认 {DEFAULT_RUN_TIME})")
    parser.add_argument("--outdir", "-o", type=str, default=OUTPUT_DIR, help="输出目录 (默认脚本所在目录)")
    args = parser.parse_args()

    if args.now:
        log("立即执行模式...")
        execute_task(args.outdir)
    else:
        log("启动模式: 先执行一次，然后进入定时调度...")
        execute_task(args.outdir)
        run_schedule_loop(args.time, args.outdir)


if __name__ == "__main__":
    main()
