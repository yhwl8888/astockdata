#!/usr/bin/env python
# -*- coding: utf-8 -*-

import akshare as ak
from openai import OpenAI
import pandas as pd
import os
import fire
from loguru import logger
import inspect
import time
import random
from pathlib import Path
from datetime import datetime
from pprint import pprint
import pandas_market_calendars as mcal
from datetime import datetime, timedelta

# 1. 获取当前时间
now = datetime.now()
# 2. 格式化为：年-月-日_时-分 (例如: 2026-02-25_16-10)
timestamp = now.strftime("%Y-%m-%d_%H-%M")
api_key = os.getenv("DS_API_KEY")


a_stock_dir = "./a_stock_dir"

def _get_func_name():
    """核心包裝方法：獲取『呼叫者』的名稱"""
    # f_back 代表回到上一層呼叫者的框架
    return inspect.currentframe().f_back.f_code.co_name

class StockAnalyzer:
    def _add_exchange_prefix(self, symbol):
        if symbol.startswith(('6', '9')):
            return f"sh{symbol}"
        elif symbol.startswith(('0', '3', '2')):
            return f"sz{symbol}"
        elif symbol.startswith(('4', '8')):
            return f"bj{symbol}"
        return symbol

    def _get_csi300_codes(self):
        return ak.index_stock_cons_csindex(symbol="000300")["成分券代码"].unique()
    def _get_csi500_codes(self):
        return ak.index_stock_cons_csindex(symbol="000500")["成分券代码"].unique()

    def _get_recent_trade_days(self, window=5):
        sse = mcal.get_calendar('SSE')
        now = datetime.now()
        end_date = now.strftime('%Y-%m-%d')

        start_date = (now - timedelta(days=20)).strftime('%Y-%m-%d')
        schedule = sse.schedule(start_date=start_date, end_date=end_date)

        all_trading_days = schedule.index.strftime('%Y%m%d').tolist()
        return all_trading_days[-window:]

    def _get_board(self):
        stock_board_industry_summary_ths_df = ak.stock_board_industry_summary_ths()
        df = stock_board_industry_summary_ths_df
        top_gainers = df.nlargest(8, '涨跌幅')
        top_losers = df.nsmallest(8, '涨跌幅')
        top_volume = df.nlargest(4, '总成交额')
        final_selection = pd.concat([top_gainers, top_losers, top_volume]).drop_duplicates(subset=['板块'])
        final_selection = final_selection.sort_values(by='涨跌幅', ascending=False)
        core_columns = ['板块', '涨跌幅', '总成交额', '净流入', '上涨家数', '下跌家数', '领涨股', '领涨股-涨跌幅']
        _ret = final_selection[core_columns]
        return _ret

    def _get_north_money(self):
        try:
            df_1d = ak.stock_hsgt_board_rank_em(symbol="北向资金增持行业板块排行", indicator="今日")
            df_3d = ak.stock_hsgt_board_rank_em(symbol="北向资金增持行业板块排行", indicator="3日")
            df_5d = ak.stock_hsgt_board_rank_em(symbol="北向资金增持行业板块排行", indicator="5日")
            df_10d = ak.stock_hsgt_board_rank_em(symbol="北向资金增持行业板块排行", indicator="10日")
        except Exception as e:
            print(f"数据获取失败: {e}")
            df_1d = df_3d = df_5d = df_10d = pd.DataFrame()  # 确保后续代码能运行

        dfs = {'1D': df_1d, '3D': df_3d, '5D': df_5d, '10D': df_10d}
        core_col = '北向资金今日增持估计-市值'

        # 1. 预处理：统一索引并清洗数据
        for key in dfs:
            dfs[key] = dfs[key].set_index('名称')
            # 确保数值化，处理可能的空值
            dfs[key][core_col] = pd.to_numeric(dfs[key][core_col], errors='coerce').fillna(0)

        # 2. 获取所有出现在 1D 榜单中的候选板块
        all_candidates = df_1d['名称'].unique()
        summary_list = []

        for name in all_candidates:
            try:
                # 基础指标
                price_1d = dfs['1D'].loc[name, '最新涨跌幅']
                v1 = dfs['1D'].loc[name, core_col]
                v3 = dfs['3D'].loc[name, core_col] if name in dfs['3D'].index else 0
                v5 = dfs['5D'].loc[name, core_col] if name in dfs['5D'].index else 0
                v10 = dfs['10D'].loc[name, core_col] if name in dfs['10D'].index else 0

                # 计算一致性（上榜次数）
                consistency = sum([name in dfs[k].index for k in dfs])

                # 计算进攻动能 (今日买入 vs 近期日均)
                avg_recent = (v5 / 5) if v5 > 0 else (v1 / 1)
                momentum = round(v1 / avg_recent, 2) if avg_recent > 0 else 0

                # --- 核心过滤逻辑 (只留以下三种情况) ---
                is_strong_attack = (v1 > 5e8 and momentum > 1.2)  # 1. 强力进攻：买入超5亿且在加速
                is_gold_pit = (price_1d < -2.0 and v1 > 2e8)      # 2. 黄金坑：大跌超过2%但外资买入超2亿
                is_long_term = (consistency >= 3 and v10 > 10e8)  # 3. 长线基调：至少3次上榜且10日买入超10亿

                if is_strong_attack or is_gold_pit or is_long_term:
                    summary_list.append({
                        "板块": name,
                        "今日涨跌%": price_1d,
                        "今日流入(亿)": round(v1 / 1e8, 2),
                        "10日总流入(亿)": round(v10 / 1e8, 2),
                        "进攻动能": momentum,
                        "稳定性": f"{consistency}/4",
                        "性质": "强力进攻" if is_strong_attack else ("黄金坑" if is_gold_pit else "长线核心"),
                        "领涨/增持股": dfs['1D'].loc[name, '今日增持最大股-市值']
                    })
            except:
                continue

        # 3. 排序并只取前 12 名（最精华的部分）
        final_df = pd.DataFrame(summary_list)
        if not final_df.empty:
            # 优先排“黄金坑”和“强力进攻”，这些更有短线爆发力
            final_df = final_df.sort_values(by=['进攻动能', '今日流入(亿)'], ascending=False).head(12)
        return final_df

    def _get_etf(self):
        least = self._get_recent_trade_days(1)
        df_etf = ak.fund_etf_spot_ths(date=least[0])
        # 2. 数据清洗与类型转换
        # 将百分比和数值字符串转换为 float，处理可能的空值
        df_etf['增长率'] = pd.to_numeric(df_etf['增长率'], errors='coerce').fillna(0)
        df_etf['当前-单位净值'] = pd.to_numeric(df_etf['当前-单位净值'], errors='coerce').fillna(0)
        df_etf['前一日-单位净值'] = pd.to_numeric(df_etf['前一日-单位净值'], errors='coerce').fillna(0)

        # 3. 核心过滤逻辑：三道防火墙
        # 第一道：申赎状态过滤 (剔除不可交易的品种)
        # 只保留“开放申购”和“开放赎回”的基金，避免 LLM 分析无法买入的标的
        mask_open = (df_etf['申购状态'].str.contains('开放', na=False)) & \
                    (df_etf['赎回状态'].str.contains('开放', na=False))
        df_active = df_etf[mask_open].copy()

        # 第二道：极端波动筛选 (捕捉市场最强信号)
        # 选取今日增长率最高的前 8 名（领涨强力品种）
        # 选取今日增长率最低的前 8 名（潜在的“黄金坑”超跌反弹品种）
        top_gainers = df_active.nlargest(8, '增长率')
        top_losers = df_active.nsmallest(8, '增长率')

        # 第三道：类型聚焦 (可选)
        # 如果你只关注股票型或指数型，可以取消下面这行的注释
        # df_active = df_active[df_active['基金类型'].str.contains('指数|股票', na=False)]

        # 合并结果并去重
        df_final = pd.concat([top_gainers, top_losers]).drop_duplicates()

        # 4. 字段瘦身与单位转化 (节省 LLM Token)
        # 丢弃重复的“最新-单位净值”和冗余的“序号”、“查询日期”
        keep_cols = [
            '基金代码', '基金名称', '当前-单位净值', 
            '增长率', '基金类型', '最新-交易日'
        ]

        # 如果表头中存在“增长值”，也保留用于辅助判断绝对强度
        if '增长值' in df_active.columns:
            keep_cols.insert(4, '增长值')

        final_report = df_final[keep_cols].copy()

        # 5. 排序：按增长率升序排列（先看跌深的黄金坑，再看领涨品种）
        final_report = final_report.sort_values(by='增长率', ascending=True)
        return final_report

    def smart_money(self):
        func_name = _get_func_name()
        logger.info(f"executing function : {func_name}")
        _md = f"{a_stock_dir}/{func_name}.md"
        with open(_md, "w", encoding="utf-8") as f:
            pass

        board_df = self._get_board()
        with open(_md, "a", encoding="utf-8") as f:
            f.write("\n# 同花顺 板块行情: 涨幅前 8 名 + 跌幅前 8 名 + 成交额最大的 4 个行业\n")
            f.write("\n")
            f.write(board_df.to_markdown(index=False, tablefmt="github"))

        north_df = self._get_north_money()
        with open(_md, "a", encoding="utf-8") as f:
            f.write("\n# 北向资金【脱水精华】分析快照\n")
            f.write("\n")
            f.write(north_df.to_markdown(index=False, tablefmt="github"))


        etf_df = self._get_etf()
        with open(_md, "a", encoding="utf-8") as f:
            f.write("\n# 同花顺 ETF 行情\n")
            f.write("\n")
            f.write(etf_df.to_markdown(index=False, tablefmt="github"))

    def update(self):
        func_name = _get_func_name()
        logger.info(f"executing function : {func_name}")
        csi300_codes = self._get_csi300_codes()
        _dir = f"{a_stock_dir}/{func_name}"
        os.makedirs(_dir, exist_ok=True)
        # 設定參數
        max_retries = 3  # 最多重試 3 次
        retry_delay = 30 # 發生異常時休眠 30 秒

        for index, code in enumerate(csi300_codes, start=1):
            logger.info(f"Fetching data for {code} ({index}/{len(csi300_codes)})")
            symbol = self._add_exchange_prefix(code)
            file_path = f"{_dir}/{symbol}.csv"

            # 1. 檢查檔案是否已存在 (斷點續傳)
            if os.path.exists(file_path):
                logger.info(f"{symbol} data already exists, skipping...")
                continue

            # 2. 開始抓取邏輯，加入重試機制
            retries = 0
            success = False

            while retries < max_retries and not success:
                try:
                    logger.info(f"Fetching data for {symbol} (Attempt {retries + 1})")

                    # 執行抓取
                    _df = ak.stock_zh_index_daily(symbol=symbol)

                    # 保存檔案
                    _df.to_csv(file_path, index=False)

                    # 成功後標記並給予一個隨機的小休眠，防止請求過快
                    success = True
                    time.sleep(random.uniform(1.0, 2.0))

                except Exception as e:
                    retries += 1
                    logger.error(f"Error fetching {symbol}: {e}")

                    if retries < max_retries:
                        logger.info(f"Sleeping for {retry_delay} seconds before retrying...")
                        time.sleep(retry_delay)
                    else:
                        logger.error(f"Max retries reached for {symbol}. Moving to next code.")


    def _llm_analyze_entry(self, prompt):
        # 初始化客户端
        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com" # 必须指定为 DeepSeek 的地址
        )

        # 发送问题
        response = client.chat.completions.create(
            model="deepseek-chat",  # 或者使用 "deepseek-reasoner"
            messages=[
                {"role": "system", "content": "你是一个专业的量化分析助手。"},
                {"role": "user", "content": prompt}
            ],
            stream=False  # 设置为 True 可以实现打字机流式效果
        )

        return response.choices[0].message.content

    def llm_analyze(self):
        func_name = _get_func_name()
        logger.info(f"executing function : {func_name}")
        _dir = f"{a_stock_dir}/{func_name}_{timestamp}"
        os.makedirs(_dir, exist_ok=True)


        data_dir = Path(f"{a_stock_dir}/my_stock_zh_index_daily")
        for i, csv_file in enumerate(data_dir.glob("*.csv"), start=1):
            logger.info(f"[{i}] 正在处理文件: {csv_file.name}")

            # 直接读取
            _df = pd.read_csv(csv_file)
            recent = _df.tail(60)[['date', 'open', 'high', 'low', 'close', 'volume']]
            data_str = recent.to_string(index=False)

            prompt = f"请分析{csv_file.stem}最近60个交易日的走势：\n\n{data_str}\n\n请给出趋势研判和支撑压力位。并给出空仓和持仓时的操作建议, 在开头给出最新日期"
            _ret = self._llm_analyze_entry(prompt)
            # _ret = "test response"

            _md_dir = f"{_dir}/{csv_file.stem}.md"
            with open(_md_dir, "w", encoding="utf-8") as f:
                f.write(_ret)

    def get_common_prompts(self):
        func_name = _get_func_name()
        logger.info(f"executing function : {func_name}")

if __name__ == '__main__':
    fire.Fire(StockAnalyzer)
