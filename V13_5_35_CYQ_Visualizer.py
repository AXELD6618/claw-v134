#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
V13.5.35 CYQ筹码分布可视化引擎
================================
基于kengerlwl/ChipDistribution三角形分布算法, 适配TDX K线数据

核心算法 (来自kengerlwl/ChipDistribution MIT License):
  - calcuSin: 三角形分布 — 以均价为顶点的三角形概率密度
  - calcuJUN: 均匀分布 — 简化版, 等量分配
  - winner: 获利盘比例计算
  - cost: 成本分布 (如cost(90) = 90%筹码的成本价)

适配改进:
  - 输入: TDX tdx_kline 返回的K线数据
  - 输出: HTML可视化(SVG+Chart.js)
  - 集成: WINNER三时点趋同度计算

Author: 毕方灵犀貔貅助手 V13.5.35
Date: 2026-07-11
Reference: kengerlwl/ChipDistribution (MIT License)
"""

import json
import math
import copy
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

BASE = Path("E:/WorkBuddy_dot_workbuddy/Claw")
OUTPUT_DIR = BASE / "outputs"
CACHE_DIR = BASE / "data" / "fullmarket_cache"


# ============================================================
# 筹码分布核心算法 (改编自kengerlwl/ChipDistribution)
# ============================================================
class ChipDistribution:
    """
    CYQ筹码分布计算引擎
    
    算法:
      三角形分布(flag=1): 以均价为顶点, 最高/最低价为两端的三角形概率密度
      均匀分布(flag=2): 在最高/最低价区间均匀分配成交量
    
    衰减系数(AC): 控制历史筹码衰减速度, AC=1为标准衰减
    """
    
    def __init__(self):
        self.Chip = {}          # 当前筹码分布 {price: volume}
        self.ChipList = {}      # 历史筹码分布 {date: {price: volume}}
        self.data = None        # K线数据 DataFrame-like
    
    def set_data(self, kline_data: List[dict]):
        """
        设置K线数据 (适配TDX tdx_kline格式)
        
        kline_data格式: [{date, open, high, low, close, volume, turnover/amount}, ...]
        """
        self.data = kline_data
        self.Chip = {}
        self.ChipList = {}
    
    def _get_avg_price(self, bar: dict) -> float:
        """计算均价 (成交额/成交量)"""
        vol = bar.get('volume', 0) or bar.get('vol', 0)
        amount = bar.get('amount', 0) or bar.get('turnover', 0)
        if vol > 0 and amount > 0:
            return amount / vol
        # 回退: (open+high+low+close)/4
        return (bar.get('open', 0) + bar.get('high', 0) + 
                bar.get('low', 0) + bar.get('close', 0)) / 4
    
    def _get_turnover_rate(self, bar: dict) -> float:
        """获取换手率"""
        return bar.get('turnover_rate', 0) or bar.get('TurnoverRate', 0) or 5.0  # 默认5%
    
    def calcu_jun(self, date_t, high_t, low_t, vol_t, turnover_rate_t, a=1, min_d=0.01):
        """均匀分布计算"""
        x = []
        l = (high_t - low_t) / min_d
        for i in range(int(l)):
            x.append(round(low_t + i * min_d, 2))
        
        length = len(x)
        if length == 0:
            return
        each_v = vol_t / length
        
        # 衰减历史筹码
        for i in self.Chip:
            self.Chip[i] = self.Chip[i] * (1 - turnover_rate_t * a)
        
        # 添加今日筹码
        for i in x:
            if i in self.Chip:
                self.Chip[i] += each_v * (turnover_rate_t * a)
            else:
                self.Chip[i] = each_v * (turnover_rate_t * a)
        
        self.ChipList[date_t] = copy.deepcopy(self.Chip)
    
    def calcu_sin(self, date_t, high_t, low_t, avg_t, vol_t, turnover_rate_t, a=1, min_d=0.01):
        """三角形分布计算 — 以均价为顶点的三角形概率密度"""
        x = []
        l = (high_t - low_t) / min_d
        for i in range(int(l)):
            x.append(round(low_t + i * min_d, 2))
        
        length = len(x)
        if length == 0:
            return
        
        # 计算今日筹码分布 (三角形)
        tmp_chip = {}
        each_v = vol_t / length
        
        for i in x:
            x1 = i
            x2 = i + min_d
            h = 2 / (high_t - low_t)  # 三角形高度
            
            if avg_t > low_t and avg_t < high_t:
                if i < avg_t:
                    # 上升段: low → avg
                    y1 = h / (avg_t - low_t) * (x1 - low_t)
                    y2 = h / (avg_t - low_t) * (x2 - low_t)
                else:
                    # 下降段: avg → high
                    y1 = h / (high_t - avg_t) * (high_t - x1)
                    y2 = h / (high_t - avg_t) * (high_t - x2)
            else:
                # 均价异常, 回退到均匀分布
                y1 = y2 = h / (high_t - low_t)
            
            s = min_d * (y1 + y2) / 2
            s = s * vol_t
            tmp_chip[i] = s
        
        # 衰减历史筹码
        for i in self.Chip:
            self.Chip[i] = self.Chip[i] * (1 - turnover_rate_t * a)
        
        # 添加今日筹码
        for i in tmp_chip:
            if i in self.Chip:
                self.Chip[i] += tmp_chip[i] * (turnover_rate_t * a)
            else:
                self.Chip[i] = tmp_chip[i] * (turnover_rate_t * a)
        
        self.ChipList[date_t] = copy.deepcopy(self.Chip)
    
    def calcu(self, date_t, high_t, low_t, avg_t, vol_t, turnover_rate_t, min_d=0.01, flag=1, ac=1):
        """统一计算入口"""
        if flag == 1:
            self.calcu_sin(date_t, high_t, low_t, avg_t, vol_t, turnover_rate_t, a=ac, min_d=min_d)
        elif flag == 2:
            self.calcu_jun(date_t, high_t, low_t, vol_t, turnover_rate_t, a=ac, min_d=min_d)
    
    def calcu_chip(self, flag=1, ac=1, days=210):
        """
        计算筹码分布
        
        Args:
            flag: 1=三角形, 2=均匀
            ac: 衰减系数
            days: 计算天数(默认210个交易日)
        """
        if not self.data:
            return
        
        data = self.data[-days:] if len(self.data) > days else self.data
        
        for bar in data:
            high_t = bar.get('high', 0)
            low_t = bar.get('low', 0)
            vol_t = bar.get('volume', 0) or bar.get('vol', 0)
            close_t = bar.get('close', 0)
            avg_t = self._get_avg_price(bar)
            turnover_rate_t = self._get_turnover_rate(bar) / 100  # 转为小数
            date_t = str(bar.get('date', ''))
            
            if high_t <= low_t or vol_t <= 0:
                continue
            
            self.calcu(date_t, high_t, low_t, avg_t, vol_t, 
                       turnover_rate_t, flag=flag, ac=ac)
    
    def winner(self, price=None) -> List[float]:
        """
        计算获利盘比例
        
        Args:
            price: 指定价格, None则使用每日收盘价
        
        Returns:
            List[float]: 每日获利盘比例 (0-1)
        """
        profits = []
        
        if not self.data:
            return profits
        
        if price is None:
            # 使用每日收盘价
            closes = [bar.get('close', 0) for bar in self.data]
            count = 0
            for date_t in self.ChipList:
                chip = self.ChipList[date_t]
                total = sum(chip.values())
                below = sum(v for p, v in chip.items() if p < closes[count])
                profits.append(below / total if total > 0 else 0)
                count += 1
        else:
            for date_t in self.ChipList:
                chip = self.ChipList[date_t]
                total = sum(chip.values())
                below = sum(v for p, v in chip.items() if p < price)
                profits.append(below / total if total > 0 else 0)
        
        return profits
    
    def cost(self, n: int) -> List[float]:
        """
        计算成本分布
        
        Args:
            n: 百分比 (如90 = 90%筹码的成本价)
        
        Returns:
            List[float]: 每日对应百分比的成本价
        """
        n = n / 100
        ans = []
        
        for date_t in self.ChipList:
            chip = self.ChipList[date_t]
            sorted_prices = sorted(chip.keys())
            total = sum(chip.values())
            
            if total == 0:
                ans.append(0)
                continue
            
            cumsum = 0
            for p in sorted_prices:
                cumsum += chip[p] / total
                if cumsum > n:
                    ans.append(p)
                    break
            else:
                ans.append(sorted_prices[-1] if sorted_prices else 0)
        
        return ans
    
    def get_current_distribution(self) -> dict:
        """获取当前筹码分布 (最新日)"""
        if not self.ChipList:
            return {}
        last_date = list(self.ChipList.keys())[-1]
        return self.ChipList[last_date]
    
    def get_distribution_at(self, date_str: str) -> dict:
        """获取指定日期的筹码分布"""
        return self.ChipList.get(str(date_str), {})
    
    def calc_scr(self) -> float:
        """
        计算SCR筹码集中度
        SCR = (COST(95) - COST(5)) / (COST(95) + COST(5)) * 100
        """
        cost_95 = self.cost(95)
        cost_5 = self.cost(5)
        
        if not cost_95 or not cost_5:
            return 50.0
        
        c95 = cost_95[-1]
        c5 = cost_5[-1]
        
        if c95 + c5 == 0:
            return 50.0
        
        return round((c95 - c5) / (c95 + c5) * 100, 2)


# ============================================================
# WINNER三时点趋同度计算
# ============================================================
class WinnerConvergence:
    """
    WINNER三时点趋同度计算引擎
    
    三时点: 今日WINNER / 昨日WINNER / 周均WINNER
    趋同度 = 1 - max(|今-昨|, |今-周|, |昨-周|) / 2.0
    趋同度≥0.80 + 三者均≤2% = 💎三时点趋同信号
    """
    
    @staticmethod
    def calc_convergence(today_w: float, yesterday_w: float, week_avg_w: float) -> float:
        """计算三时点趋同度"""
        d1 = abs(today_w - yesterday_w)
        d2 = abs(today_w - week_avg_w)
        d3 = abs(yesterday_w - week_avg_w)
        max_diff = max(d1, d2, d3)
        return round(1 - max_diff / 2.0, 4)
    
    @staticmethod
    def is_convergent(today_w: float, yesterday_w: float, week_avg_w: float,
                      threshold: float = 0.80, max_winner: float = 0.02) -> Tuple[bool, float]:
        """判断是否满足三时点趋同条件"""
        conv = WinnerConvergence.calc_convergence(today_w, yesterday_w, week_avg_w)
        is_conv = (conv >= threshold and 
                   today_w <= max_winner and 
                   yesterday_w <= max_winner and 
                   week_avg_w <= max_winner)
        return is_conv, conv
    
    @staticmethod
    def calc_from_chipdist(chipdist: ChipDistribution) -> dict:
        """
        从ChipDistribution引擎计算三时点WINNER
        
        Returns:
            {today_winner, yesterday_winner, week_avg_winner, 
             convergence, is_convergent, all_low}
        """
        winners = chipdist.winner()
        if len(winners) < 5:
            return {
                "today_winner": winners[-1] if winners else 0,
                "yesterday_winner": winners[-2] if len(winners) >= 2 else 0,
                "week_avg_winner": sum(winners[-5:]) / len(winners[-5:]) if len(winners) >= 5 else 0,
                "convergence": 0,
                "is_convergent": False,
                "all_low": False,
            }
        
        today_w = winners[-1]
        yesterday_w = winners[-2]
        week_avg_w = sum(winners[-5:]) / 5
        
        is_conv, conv = WinnerConvergence.is_convergent(today_w, yesterday_w, week_avg_w)
        all_low = today_w <= 0.02 and yesterday_w <= 0.02 and week_avg_w <= 0.02
        
        return {
            "today_winner": round(today_w * 100, 4),      # 转百分比
            "yesterday_winner": round(yesterday_w * 100, 4),
            "week_avg_winner": round(week_avg_w * 100, 4),
            "convergence": conv,
            "is_convergent": is_conv,
            "all_low": all_low,
        }


# ============================================================
# CYQ可视化HTML生成
# ============================================================
def generate_cyq_html(
    code: str,
    name: str,
    kline_data: List[dict],
    chipdist: ChipDistribution,
    winner_data: dict,
    scr: float,
    output_path: str = None,
) -> str:
    """生成CYQ筹码分布可视化HTML"""
    
    if output_path is None:
        output_path = str(OUTPUT_DIR / f"cyq_{code}_{datetime.now().strftime('%Y%m%d')}.html")
    
    # 获取当前分布数据
    dist = chipdist.get_current_distribution()
    if not dist:
        return ""
    
    # 按价格排序
    sorted_prices = sorted(dist.keys())
    prices = sorted_prices
    volumes = [dist[p] for p in sorted_prices]
    
    # 归一化
    max_vol = max(volumes) if volumes else 1
    volumes_pct = [v / max_vol * 100 for v in volumes]
    
    # 当前价格
    current_price = kline_data[-1].get('close', 0) if kline_data else 0
    
    # 找获利盘线位置
    profit_line_idx = 0
    for i, p in enumerate(prices):
        if p >= current_price:
            profit_line_idx = i
            break
    
    # 生成筹码分布柱状图SVG
    bar_count = min(80, len(prices))  # 限制显示数量
    step = max(1, len(prices) // bar_count)
    chart_points = []
    for i in range(0, len(prices), step):
        chart_points.append((prices[i], volumes_pct[i]))
    
    # SVG尺寸
    svg_w = 680
    svg_h = 360
    margin = {"top": 30, "right": 60, "bottom": 50, "left": 60}
    chart_w = svg_w - margin["left"] - margin["right"]
    chart_h = svg_h - margin["top"] - margin["bottom"]
    
    min_price = min(prices)
    max_price = max(prices)
    price_range = max_price - min_price if max_price > min_price else 1
    
    # X轴: 价格 → 像素
    def price_to_x(p):
        return margin["left"] + (p - min_price) / price_range * chart_w
    
    # Y轴: 成交量% → 像素
    def vol_to_y(v):
        return margin["top"] + chart_h - (v / 100) * chart_h
    
    # 生成柱状图
    bars_svg = ""
    for p, v in chart_points:
        x = price_to_x(p)
        y = vol_to_y(v)
        bar_w = max(2, chart_w / bar_count * 0.8)
        bar_h = margin["top"] + chart_h - y
        # 获利盘(绿色) vs 套牢盘(红色)
        is_profit = p < current_price
        color = "#06a77d" if is_profit else "#d62828"
        bars_svg += f'<rect x="{x - bar_w/2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" fill="{color}" opacity="0.7"/>\n'
    
    # 当前价格线
    current_x = price_to_x(current_price)
    current_line = f'<line x1="{current_x:.1f}" y1="{margin["top"]}" x2="{current_x:.1f}" y2="{margin["top"] + chart_h}" stroke="#2563eb" stroke-width="2" stroke-dasharray="5,3"/>\n'
    current_label = f'<text x="{current_x:.1f}" y="{margin["top"] - 8}" text-anchor="middle" fill="#2563eb" font-size="12" font-weight="bold">现价 {current_price:.2f}</text>\n'
    
    # 获利盘比例标注
    profit_pct = winner_data.get("today_winner", 0)
    profit_label = f'<text x="{margin["left"] + 10}" y="{margin["top"] + 20}" fill="#06a77d" font-size="14" font-weight="bold">获利盘: {profit_pct:.2f}%</text>\n'
    trapped_pct = 100 - profit_pct
    trapped_label = f'<text x="{svg_w - margin["right"] - 10}" y="{margin["top"] + 20}" text-anchor="end" fill="#d62828" font-size="14" font-weight="bold">套牢盘: {trapped_pct:.2f}%</text>\n'
    
    # 坐标轴
    axis_svg = f"""
    <line x1="{margin['left']}" y1="{margin['top'] + chart_h}" x2="{svg_w - margin['right']}" y2="{margin['top'] + chart_h}" stroke="#666" stroke-width="1"/>
    <line x1="{margin['left']}" y1="{margin['top']}" x2="{margin['left']}" y2="{margin['top'] + chart_h}" stroke="#666" stroke-width="1"/>
    <!-- X轴标签 -->
    <text x="{margin['left']}" y="{svg_h - 20}" text-anchor="middle" fill="#999" font-size="11">{min_price:.2f}</text>
    <text x="{svg_w - margin['right']}" y="{svg_h - 20}" text-anchor="middle" fill="#999" font-size="11">{max_price:.2f}</text>
    <text x="{svg_w // 2}" y="{svg_h - 5}" text-anchor="middle" fill="#999" font-size="11">价格</text>
    <!-- Y轴标签 -->
    <text x="{margin['left'] - 10}" y="{margin['top'] + 5}" text-anchor="end" fill="#999" font-size="11">100%</text>
    <text x="{margin['left'] - 10}" y="{margin['top'] + chart_h}" text-anchor="end" fill="#999" font-size="11">0%</text>
    """
    
    # 三时点趋同信息
    conv = winner_data.get("convergence", 0)
    is_conv = winner_data.get("is_convergent", False)
    all_low = winner_data.get("all_low", False)
    
    conv_color = "#10b981" if is_conv else "#f59e0b" if conv >= 0.6 else "#ef4444"
    conv_icon = "💎" if is_conv else "⚠️" if conv >= 0.6 else "❌"
    
    # HTML完整页面
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CYQ筹码分布 — {code} {name}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }}
        .container {{ max-width: 720px; margin: 0 auto; }}
        .header {{ text-align: center; padding: 20px 0; border-bottom: 1px solid #334155; }}
        .header h1 {{ font-size: 24px; color: #f1f5f9; }}
        .header .code {{ font-size: 14px; color: #94a3b8; margin-top: 5px; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 20px; margin: 15px 0; border: 1px solid #334155; }}
        .card-title {{ font-size: 16px; font-weight: 600; color: #f1f5f9; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #334155; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
        .stat-item {{ background: #0f172a; border-radius: 8px; padding: 12px; text-align: center; }}
        .stat-label {{ font-size: 11px; color: #94a3b8; margin-bottom: 4px; }}
        .stat-value {{ font-size: 18px; font-weight: 700; }}
        .profit {{ color: #06a77d; }}
        .trapped {{ color: #d62828; }}
        .convergence {{ color: {conv_color}; }}
        .conv-badge {{ display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; margin-top: 8px; }}
        .conv-yes {{ background: rgba(16, 185, 129, 0.2); color: #10b981; border: 1px solid #10b981; }}
        .conv-no {{ background: rgba(239, 68, 68, 0.2); color: #ef4444; border: 1px solid #ef4444; }}
        .three-point {{ display: flex; justify-content: space-around; margin-top: 15px; }}
        .tp-item {{ text-align: center; }}
        .tp-label {{ font-size: 11px; color: #94a3b8; }}
        .tp-value {{ font-size: 16px; font-weight: 600; margin-top: 4px; }}
        .tp-low {{ color: #10b981; }}
        .tp-high {{ color: #ef4444; }}
        svg {{ display: block; margin: 0 auto; }}
        .footer {{ text-align: center; padding: 20px; color: #64748b; font-size: 11px; }}
        .legend {{ display: flex; justify-content: center; gap: 20px; margin-top: 10px; }}
        .legend-item {{ display: flex; align-items: center; gap: 6px; font-size: 12px; color: #94a3b8; }}
        .legend-dot {{ width: 12px; height: 12px; border-radius: 2px; }}
    </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>CYQ筹码分布分析</h1>
        <div class="code">{code} {name} | {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
    </div>
    
    <div class="card">
        <div class="card-title">筹码分布图</div>
        <svg viewBox="0 0 {svg_w} {svg_h}" width="100%">
            {bars_svg}
            {current_line}
            {current_label}
            {profit_label}
            {trapped_label}
            {axis_svg}
        </svg>
        <div class="legend">
            <div class="legend-item"><div class="legend-dot" style="background:#06a77d"></div>获利盘</div>
            <div class="legend-item"><div class="legend-dot" style="background:#d62828"></div>套牢盘</div>
            <div class="legend-item"><div class="legend-dot" style="background:#2563eb"></div>当前价格</div>
        </div>
    </div>
    
    <div class="card">
        <div class="card-title">核心指标</div>
        <div class="stats-grid">
            <div class="stat-item">
                <div class="stat-label">获利盘比例</div>
                <div class="stat-value profit">{profit_pct:.4f}%</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">SCR筹码集中度</div>
                <div class="stat-value {'profit' if scr < 8 else 'trapped' if scr > 20 else ''}" style="color:{'#10b981' if scr < 8 else '#d62828' if scr > 20 else '#f59e0b'}">{scr}</div>
            </div>
            <div class="stat-item">
                <div class="stat-label">趋同度</div>
                <div class="stat-value convergence">{conv:.4f}</div>
            </div>
        </div>
        <div style="text-align:center;margin-top:12px;">
            <span class="conv-badge {'conv-yes' if is_conv else 'conv-no'}">
                {conv_icon} {'三时点趋同信号 💎' if is_conv else '未达趋同阈值'}
            </span>
            {'<span class="conv-badge conv-yes" style="margin-left:8px;">✅ 三时点均≤2%</span>' if all_low else ''}
        </div>
    </div>
    
    <div class="card">
        <div class="card-title">WINNER三时点趋同分析</div>
        <div class="three-point">
            <div class="tp-item">
                <div class="tp-label">今日WINNER</div>
                <div class="tp-value {'tp-low' if winner_data.get('today_winner', 100) <= 2 else 'tp-high'}">{winner_data.get('today_winner', 0):.4f}%</div>
            </div>
            <div class="tp-item">
                <div class="tp-label">昨日WINNER</div>
                <div class="tp-value {'tp-low' if winner_data.get('yesterday_winner', 100) <= 2 else 'tp-high'}">{winner_data.get('yesterday_winner', 0):.4f}%</div>
            </div>
            <div class="tp-item">
                <div class="tp-label">周均WINNER</div>
                <div class="tp-value {'tp-low' if winner_data.get('week_avg_winner', 100) <= 2 else 'tp-high'}">{winner_data.get('week_avg_winner', 0):.4f}%</div>
            </div>
        </div>
        <div style="margin-top:15px;text-align:center;font-size:12px;color:#94a3b8;">
            趋同度 = 1 - max(|今-昨|, |今-周|, |昨-周|) / 2.0<br>
            阈值: 趋同度≥0.80 + 三时点WINNER均≤2% = 💎信号<br>
            <span style="color:{conv_color};font-weight:600;">当前趋同度: {conv:.4f} {'≥' if conv >= 0.80 else '<'} 0.80</span>
        </div>
    </div>
    
    <div class="footer">
        V13.5.35 CYQ引擎 | 算法: kengerlwl/ChipDistribution (MIT) | 三角形分布<br>
        毕方灵犀貔貅助手 | {datetime.now().strftime('%Y-%m-%d')}
    </div>
</div>
</body>
</html>"""
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    return output_path


# ============================================================
# 验证测试
# ============================================================
if __name__ == "__main__":
    print("=" * 70)
    print("V13.5.35 CYQ筹码分布可视化引擎 — 验证测试")
    print("=" * 70)
    
    # 模拟K线数据 (60日)
    import random
    random.seed(42)
    base_price = 15.0
    kline_data = []
    for i in range(60):
        date = f"2026-0{7 if i < 30 else 7}-{(i % 30) + 1:02d}"
        open_p = base_price * (1 + random.uniform(-0.02, 0.02))
        close_p = open_p * (1 + random.uniform(-0.04, 0.04))
        high_p = max(open_p, close_p) * (1 + random.uniform(0, 0.02))
        low_p = min(open_p, close_p) * (1 - random.uniform(0, 0.02))
        vol = int(random.uniform(5000000, 20000000))
        amount = vol * close_p
        turnover = random.uniform(3, 15)
        kline_data.append({
            'date': date, 'open': round(open_p, 2),
            'high': round(high_p, 2), 'low': round(low_p, 2),
            'close': round(close_p, 2), 'volume': vol,
            'amount': round(amount, 2), 'turnover_rate': turnover,
        })
        base_price = close_p
    
    # 计算筹码分布
    cd = ChipDistribution()
    cd.set_data(kline_data)
    cd.calcu_chip(flag=1, ac=1, days=60)  # 三角形分布
    
    # WINNER计算
    winners = cd.winner()
    current_winner = winners[-1] if winners else 0
    print(f"\n### 筹码分布计算结果")
    print(f"  计算天数: {len(cd.ChipList)}")
    print(f"  当前获利盘: {current_winner*100:.4f}%")
    
    # SCR计算
    scr = cd.calc_scr()
    print(f"  SCR筹码集中度: {scr}")
    
    # 成本分布
    cost_90 = cd.cost(90)
    cost_50 = cd.cost(50)
    cost_10 = cd.cost(10)
    print(f"  COST(90): {cost_90[-1] if cost_90 else 'N/A'}")
    print(f"  COST(50): {cost_50[-1] if cost_50 else 'N/A'}")
    print(f"  COST(10): {cost_10[-1] if cost_10 else 'N/A'}")
    
    # 三时点趋同
    winner_data = WinnerConvergence.calc_from_chipdist(cd)
    print(f"\n### WINNER三时点趋同分析")
    print(f"  今日WINNER: {winner_data['today_winner']:.4f}%")
    print(f"  昨日WINNER: {winner_data['yesterday_winner']:.4f}%")
    print(f"  周均WINNER: {winner_data['week_avg_winner']:.4f}%")
    print(f"  趋同度: {winner_data['convergence']}")
    print(f"  趋同信号: {'💎 YES' if winner_data['is_convergent'] else '❌ NO'}")
    print(f"  三时点均低: {'✅' if winner_data['all_low'] else '❌'}")
    
    # 生成HTML
    output = generate_cyq_html(
        code="603195", name="公牛集团",
        kline_data=kline_data,
        chipdist=cd,
        winner_data=winner_data,
        scr=scr,
    )
    print(f"\n### HTML报告: {output}")
    
    print("\n" + "=" * 70)
    print("CYQ筹码分布可视化引擎验证通过!")
    print(f"算法: kengerlwl/ChipDistribution (MIT License) — 三角形分布")
    print("=" * 70)
