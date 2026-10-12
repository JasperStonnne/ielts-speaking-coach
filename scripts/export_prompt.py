#!/usr/bin/env python3
"""Build a standalone prompt from the skill's canonical bank and exam flow."""
from pathlib import Path
import argparse
import json
import re

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'skills/ielts-speaking-coach'

def build():
    bank=json.loads((SKILL/'references/question-bank.json').read_text())
    flow=(SKILL/'references/exam-flow.md').read_text()
    flow=flow.replace('完成整轮则 finish；中途停止则 pause','完成整轮标记结束；中途停止保留断点')
    flow=flow.replace('以 skip 保持未覆盖','保持未覆盖')
    flow=flow.replace('记录工具负责新题优先与到期复习','你负责新题优先与到期复习')
    flow=flow.replace('保存 review/finish 时按回答 ID 填 goal、met 和 action。met 为 false 时默认 1 天后复习，true 时 3 天后，null 时不猜日期。','复盘时按回答编号保存目标、是否达标与行动。未达标默认 1 天后复习，达标默认 3 天后，不能判断时不猜日期。')
    flow=flow.replace('Part 2 准备由工具保存真实截止时间，并等待剩余时间归零。', '宿主支持真实计时与等待时，由 AI 计时一分钟；否则如实说明无法自动计时，不用文字倒数假装时间流逝。')
    opening='''# 雅思口语陪练 · v0.1 单文件版

把本文件上传或粘贴到 AI 聊天，开启语音说“开始”。练完保存 AI 生成的完整新版同名文件，下次带上它说“继续”。

## 给 AI 的指令

读取全部规则、题库和末尾进度。未说开始时简短确认实际题库和断点。每个新语音会话先询问用户选择 Part 1、2 或 3；用户已经说出部分则直接采用，不要求填问卷。不能读文件时请用户粘贴，不假装读取成功。

这是无脚本版本，不要求运行 Python，不声称已修改用户桌面。使用现有语音能力；缺少可靠计时或原始音频时按下述边界处理。

选择 Part 后接上该部分断点或下一题，各部分分别记录位置。默认专项，一次一题；完整三段模拟仅用户要求时开启。跳过题保存为跳过并从默认自动选题中排除，跨会话从后续题开始；用户明确指定重练该题才重新问。用户指定优先，其次后续新题，再到期复习；新题用完说明复习原因，不清空进度。Part 1/3 专项回答后也先点评，可同题重答，再进入下一题。

每轮使用 S0001、S0002 等编号，每条有效主回答使用 S0001-A01 等编号。按记录编号去重，不因重复导出增加次数。主回答才计覆盖；听题、沉默、跳过、识别失败、收尾和补充追问不计。短而有内容的回答可以记录。

没有有效回答时“下一题”表示跳过；“继续”则重述当前题。Part 2 区分准备、陈述和收尾，保存收尾序号；主陈述已保存后不会因追问中断再计一次。陈述不完整时保留断点，不补写虚构回答。

用户结束或说“导出进度”时，生成完整新版文件，规则与90题全部保留，只更新个人进度区。能生成下载文件就提供真实文件；否则返回文字界面后输出完整代码块。提示一次需要保存替换。不能提供只含档案的版本或虚构下载链接。

个人进度保持紧凑：全部题目状态表、准确断点、最近三轮简短回答记录；更早每轮压成一行摘要，保留轮号、日期、已覆盖编号和主要问题。压缩旧历史不改变题目累计次数，压缩后不声称仍保存完整逐句历史。不要追加长段原始转写，避免档案无限膨胀。用户可以保留以前下载的文件作为历史副本。

只展示练过与待复习，不判定巩固。覆盖为不同有效主问题编号数 / 当前题库总数，同题重答不增加覆盖；收尾追问不进分母。数据未知不猜测。日期按用户当地日期。新会话只能续接实际带来的最新文件，不能恢复未保存的聊天。

'''
    chunks=[opening,flow,'\n<!-- QUESTION_BANK_START -->\n# 原创练习题库\n',f"版本：{bank['version']}。{bank['source']}\n\n"]
    for q in bank['questions']:
        chunks += [f"### {q['id']} · {q['topic']}\n\n{q['text']}\n\n"]
        if q['part']==2:
            chunks += ['You should say:\n\n'+'\n'.join('- '+p for p in q['points'])+'\n\nAnd explain '+q['explain']+'\n\n',
                       '收尾追问（不另计覆盖）：\n\n'+'\n'.join('- '+p for p in q['closing'])+'\n\n',
                       '关联 Part 3：'+', '.join(q['related'])+'。\n\n']
    chunks += ['<!-- QUESTION_BANK_END -->\n\n<!-- LEARNING_RECORD_START -->\n## 我的学习进度\n\n',
               '格式：prompt-v0.1；题库：original-practice-v2；最近更新：尚未开始。\n\n',
               '| Part | 总数 | 已覆盖 | 待复习 |\n|---|---:|---:|---:|\n| Part 1 | 30 | 0 | 0 |\n| Part 2 | 10 | 0 | 0 |\n| Part 3 | 50 | 0 | 0 |\n\n',
               '当前轮：无；模式：待选择 Part 1/2/3；当前题：无；阶段：未开始；收尾序号：0；下一个轮号：S0001。\n\n',
               '各部分初始下一题：Part 1 为 P1-001；Part 2 为 P2-001；Part 3 为 P3-001。选择后再确定本轮计划。已跳过题与手动通过标记必须保留，即使真实覆盖仍为零。\n\n',
               '### 题目进度\n\n| 题号 | 状态 | 次数 | 最近日期 | 具体目标 | 下次复习 |\n|---|---|---:|---|---|---|\n\n',
               '未列出的题默认未练。暂无回答记录。\n\n### 最近三轮与早期摘要\n\n暂无。\n\n<!-- LEARNING_RECORD_END -->\n']
    text=''.join(chunks)
    assert len(re.findall(r'^### P[123]-\d{3}',text,re.M))==90
    return text

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,default=ROOT/'dist/开始陪练.md')
    args=p.parse_args()
    text=build()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(text,encoding='utf-8')
    print(json.dumps({'output':str(args.output),'characters':len(text),'bytes':len(text.encode('utf-8'))},ensure_ascii=False))
