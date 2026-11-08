#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一次性富集：把全市场发现层 TOP30 的 theme_fit 合并进 candidate_research.json。
不覆盖已有条目；仅新增/补全 TOP30。沙箱可达。"""
import os, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESEARCH_P = os.path.join(ROOT, "data", "candidate_research.json")

TOP30 = {
    "300584": dict(name="海辰药业", theme="化学制药/医药", catalyst="业绩驱动",
                   fundamental="中报营收+22% 归母高增但Q2回落;化学制药主业",
                   risk="偏离物理AI/机器人硬件主线;模型误抓(涨停标签)",
                   agent_judgment="FILTER-OUT:医药化学制药,偏离主线,剔除watchlist", theme_fit="off"),
    "300561": dict(name="汇金科技", theme="金融科技/AI安全(摘帽)", catalyst="摘帽+金融科技概念",
                   fundamental="弱:金融科技/AI安全,扭亏摘帽股",
                   risk="偏离物理AI硬件主线;题材炒作",
                   agent_judgment="FILTER-OUT:金融科技非机器人硬件,剔除watchlist", theme_fit="off"),
    "301101": dict(name="明月镜片", theme="AI眼镜光学镜片(小米独家)", catalyst="AI眼镜光学镜片小米独供",
                   fundamental="中:镜片主业;AI眼镜光学镜片切入",
                   risk="AI硬件边(AI眼镜),非机器人核心",
                   agent_judgment="Tier2弱沾边(AI眼镜光学);观察不重仓", theme_fit="weak"),
    "301699": dict(name="洛轴股份", theme="轴承龙头+人形机器人轴承", catalyst="人形机器人轴承客户验证",
                   fundamental="中:轴承龙头;机器人轴承送样验证",
                   risk="机器人轴承放量节奏待验证",
                   agent_judgment="Tier2埋伏(机器人轴承核心零部件);可追踪", theme_fit="on"),
    "688368": dict(name="晶丰明源", theme="半导体电源管理+AI服务器电源", catalyst="并购四川易冲(电源管理)",
                   fundamental="中:模拟电源管理;并购切入AI服务器电源",
                   risk="估值/整合风险;半导体国产替代弱沾边",
                   agent_judgment="Tier2弱沾边(AI服务器电源);观察", theme_fit="weak"),
    "688836": dict(name="宇树科技-W", theme="人形机器人龙头(G1/H2/H2 Plus)", catalyst="人形机器人量产交付",
                   fundamental="强:人形机器人龙头;G1消费级/H2工业级量产",
                   risk="高位;估值未上市参照",
                   agent_judgment="Tier2埋伏(人形机器人标杆);系统真edge强相关", theme_fit="on"),
    "300369": dict(name="绿盟科技", theme="网络安全/大模型安全", catalyst="AI安全概念",
                   fundamental="弱:网络安全;大模型安全新业务基数小",
                   risk="偏离物理AI硬件主线",
                   agent_judgment="FILTER-OUT:网络安全,剔除watchlist", theme_fit="off"),
    "301533": dict(name="威马农机", theme="农机", catalyst="农机概念",
                   fundamental="稳:农机制造主业",
                   risk="偏离物理AI/机器人主线;模型误抓",
                   agent_judgment="FILTER-OUT:农机,剔除watchlist", theme_fit="off"),
    "688097": dict(name="博众精工", theme="自动化设备/半导体设备/光模块/换电", catalyst="半导体设备+自动化",
                   fundamental="稳:自动化设备;半导体/光模块/换电多线",
                   risk="多线摊薄;半导体设备弱沾边",
                   agent_judgment="Tier2弱沾边(半导体设备/自动化);观察", theme_fit="weak"),
    "688499": dict(name="利元亨", theme="锂电装备+人形机器人零部件代工", catalyst="机器人零部件代工",
                   fundamental="中:锂电装备;机器人零部件代工拓展",
                   risk="锂电周期;机器人代工收入占比小",
                   agent_judgment="Tier2弱沾边(机器人零件代工);观察", theme_fit="weak"),
    "688585": dict(name="上纬新材", theme="具身智能材料平台(智元反向收购)/人形机器人", catalyst="智元机器人反向收购63.62%股份;启元Q1/T1人形机器人;物理AI数据采集HIL-UMI",
                   fundamental="中:风电材料基本盘;具身智能投入致由盈转亏(2026H1亏1.67亿);预收货款2.1亿",
                   risk="高估值(596亿)/亏损;消费级机器人商业模式未验证",
                   agent_judgment="Tier2埋伏(具身智能材料+人形机器人);题材极硬但基本面承压,小仓+硬止损", theme_fit="on"),
    "301002": dict(name="崧盛股份", theme="LED驱动电源+参股谐波减速器(重庆精刚)", catalyst="参股重庆精刚研发人形机器人旋转关节减速器",
                   fundamental="中:中大功率LED驱动电源;H1归母+5121%;重庆精刚为参股(非主业)",
                   risk="主业LED电源;机器人减速器仅参股弱沾边",
                   agent_judgment="Tier2弱沾边(参股谐波减速器);观察不重仓", theme_fit="weak"),
    "688279": dict(name="峰岹科技", theme="BLDC电机驱动芯片+机器人执行器(空心杯/关节)", catalyst="合资三花精驱(空心杯电机);FU75XX机器人关节专用芯片;物理AI感知电机驱动",
                   fundamental="强:2026前三季归母+69.69%;工业(AI散热)营收+175%;PEG<0.7",
                   risk="TTM约50倍;机器人收入基数小;减持曾压制",
                   agent_judgment="Tier2埋伏(机器人执行器电机芯片硬核);业绩兑现+题材强,可适度", theme_fit="on"),
    "300031": dict(name="宝通科技", theme="工业散货物料智能输送+游戏出海+VR/AR", catalyst="具身智能机器人布局(弱标签)",
                   fundamental="稳:H1归母+37%;游戏43%+智能输送42%双主业",
                   risk="游戏+输送带主业;机器人仅概念标签,偏离硬件主线",
                   agent_judgment="FILTER-OUT:游戏/输送带,机器人弱标签,剔除watchlist", theme_fit="off"),
    "300687": dict(name="赛意信息", theme="工业软件MES/ERP+全栈工业AI(算力-工业大模型-场景智能体)", catalyst="物理AI物流车辆智能调度;83亿算力运营订单",
                   fundamental="中:H1归母+342.87%;工业AI全栈落地;255个含AI项目交付",
                   risk="工业软件非硬件;估值弹性含算力订单",
                   agent_judgment="Tier2弱沾边(物理AI工业智能化/软件);可观察", theme_fit="weak"),
    "301580": dict(name="爱迪特", theme="口腔材料与设备数字化齿科", catalyst="无机器人/AI硬件催化",
                   fundamental="稳:数字化齿科全球服务商;174项专利",
                   risk="纯医疗齿科,偏离物理AI/机器人主线",
                   agent_judgment="FILTER-OUT:齿科,剔除watchlist", theme_fit="off"),
    "688801": dict(name="燧原科技-U", theme="国产AI算力芯片(云端训练/推理加速卡)", catalyst="腾讯第一大股东(18%);国产智算中心;2026H1营收+279%",
                   fundamental="中:AI加速卡占比76%;国产AI芯片领军;仍亏损但收窄",
                   risk="TTM为负;国产算力资本开支周期波动",
                   agent_judgment="Tier2埋伏(AI算力芯片主线);硬科技核心,可追踪", theme_fit="on"),
    "688802": dict(name="沐曦股份-U", theme="国产全栈GPU(训练/推理/图形)+具身智能X方向", catalyst="2026前三季扭亏为盈;1+6+X含具身智能",
                   fundamental="中:GPU全栈;前三季营收+105~151%;归母扭亏5.9~7.2亿",
                   risk="TTM极高(21946);GPU价格战/生态",
                   agent_judgment="Tier2埋伏(AI算力GPU主线);硬科技核心,可追踪", theme_fit="on"),
    "688023": dict(name="安恒信息", theme="AI+网络安全/数据安全", catalyst="无机器人硬件催化",
                   fundamental="弱:网络安全;恒脑安全智能体",
                   risk="偏离物理AI硬件/机器人主线",
                   agent_judgment="FILTER-OUT:网络安全,剔除watchlist", theme_fit="off"),
    "301448": dict(name="开创电气", theme="手持式电动工具(切割/打磨/钻孔)", catalyst="无机器人/AI催化",
                   fundamental="稳:电动工具制造商;H1归母+140%",
                   risk="纯电动工具,偏离主线;模型误抓",
                   agent_judgment="FILTER-OUT:电动工具,剔除watchlist", theme_fit="off"),
    "300893": dict(name="松原安全", theme="汽车被动安全(安全带/气囊/方向盘)", catalyst="无机器人硬件催化",
                   fundamental="稳:汽车被动安全龙头;毛利率>33%;全球化基地",
                   risk="偏离物理AI/机器人主线(仅均胜等同行布局机器人)",
                   agent_judgment="FILTER-OUT:汽车安全件,剔除watchlist", theme_fit="off"),
    "300454": dict(name="深信服", theme="网络安全+云计算+安全具身智能", catalyst="与吉翼具身智能签'安全+具身智能'合作(机器人固件安全)",
                   fundamental="稳:网络安全/云计算创新标杆;云业务强",
                   risk="主业安全/云;机器人仅安全赋能边",
                   agent_judgment="Tier2弱沾边(安全+具身智能);观察", theme_fit="weak"),
    "300709": dict(name="精研科技", theme="MIM精密件+折叠屏铰链+灵巧手(电机齿轮箱)+散热(液冷/光模块)", catalyst="战略布局灵巧手领域;扫地机器人电机齿轮箱",
                   fundamental="中:全球MIM头部;折叠屏铰链+散热+精研动力(传动)",
                   risk="灵巧手收入基数小;消费电子周期",
                   agent_judgment="Tier2弱沾边(机器人灵巧手零件);观察", theme_fit="weak"),
    "688207": dict(name="格灵深瞳", theme="计算机视觉/多模态大模型+机器人感知与控制", catalyst="机器人感知与控制技术;金砖安防智算",
                   fundamental="弱:计算机视觉首家AI上市公司;金融/城市管理中落地",
                   risk="视觉AI软件非硬件;偏离物理AI硬件但机器人感知沾边",
                   agent_judgment="Tier2弱沾边(机器人视觉感知);观察", theme_fit="weak"),
    "300033": dict(name="同花顺", theme="金融信息+AI投顾", catalyst="无机器人/AI硬件催化",
                   fundamental="稳:互联网金融信息龙头;AI投资助手Aime",
                   risk="偏离物理AI/机器人主线",
                   agent_judgment="FILTER-OUT:金融信息,剔除watchlist", theme_fit="off"),
    "688583": dict(name="思看科技", theme="3D视觉扫描+机器人视觉(与艾利特机器人'视觉+机器人'合作)", catalyst="3D激光扫描;艾利特战略合作(视觉+机器人)",
                   fundamental="中:三维视觉数字化龙头;工业检测/计量",
                   risk="3D视觉非本体硬件;机器人视觉弱沾边",
                   agent_judgment="Tier2弱沾边(机器人3D视觉);观察", theme_fit="weak"),
    "688628": dict(name="优利德", theme="测试测量仪器(下游含具身智能/算力电源)", catalyst="无机器人硬件本体",
                   fundamental="稳:仪器仪表小巨人;四大研发中心",
                   risk="仅是测试仪器下游应用,非机器人硬件",
                   agent_judgment="FILTER-OUT:测试仪器,剔除watchlist", theme_fit="off"),
    "688381": dict(name="帝奥微", theme="模拟芯片(信号链+电源管理)应用于机器人/光模块", catalyst="机器人应用标签;IATF16949车规",
                   fundamental="中:高性能模拟芯片;机器人/汽车电子/光模块",
                   risk="模拟芯片弱沾机器人主线;国产替代",
                   agent_judgment="Tier2弱沾边(机器人模拟芯片);观察", theme_fit="weak"),
    "300825": dict(name="阿尔特", theme="汽车设计+人形机器人研发(英伟达SolutionAdvisor/富士康Turn-Key)", catalyst="英伟达合作;富士康Turn-Key机器人平台;轮式/水下/钻井机器人",
                   fundamental="中:H1归母+144.98%;整车研发+机器人公司",
                   risk="机器人研发处早期;主业汽车设计",
                   agent_judgment="Tier2弱沾边(人形机器人研发);观察", theme_fit="weak"),
    "688017": dict(name="绿的谐波", theme="谐波减速器龙头(人形机器人核心零部件)", catalyst="谐波减速器批量出口;国内外人形机器人客户合作",
                   fundamental="强:H1营收+38.64% 归母+31.25%;谐波减速器占比82.67%",
                   risk="TTM高;人形机器人放量节奏",
                   agent_judgment="Tier2埋伏(谐波减速器硬核核心零部件);系统真edge强相关,可适度", theme_fit="on"),
}

def main():
    research = {}
    if os.path.exists(RESEARCH_P):
        research = json.load(open(RESEARCH_P, encoding="utf-8"))
    added, updated = 0, 0
    for code, d in TOP30.items():
        d = dict(d)
        d["source_date"] = "2026-10-25"
        if code in research:
            # 仅补全缺失字段，不覆盖既有研判
            for k, v in d.items():
                if k not in research[code] or research[code].get(k) in (None, "", "?"):
                    research[code][k] = v
            updated += 1
        else:
            research[code] = d
            added += 1
    json.dump(research, open(RESEARCH_P, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("[enrich] TOP30 合并完成: 新增=%d 补全=%d 总计=%d" % (added, updated, len(research)))
    on = sum(1 for c in TOP30 if TOP30[c]["theme_fit"] == "on")
    weak = sum(1 for c in TOP30 if TOP30[c]["theme_fit"] == "weak")
    off = sum(1 for c in TOP30 if TOP30[c]["theme_fit"] == "off")
    print("[enrich] TOP30 分类: on=%d weak=%d off=%d" % (on, weak, off))

if __name__ == "__main__":
    main()
