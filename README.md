<p align="center">
  <img src="assets/logo.png" width="200" alt="IELTS Speaking Coach logo: a speech bubble, audio waveform and continuous loop" />
</p>

<h1 align="center">IELTS Speaking Coach</h1>

<p align="center">让每一次 AI 语音陪练，都接得上上一次。</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-22c55e" alt="MIT License" /></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB" alt="Python 3.9 or later" />
  <a href="https://github.com/JasperStonnne/ielts-speaking-coach/actions/workflows/test.yml"><img src="https://github.com/JasperStonnne/ielts-speaking-coach/actions/workflows/test.yml/badge.svg" alt="Tests" /></a>
</p>

基于现有语音 AI 的开源雅思口语陪练 skill。先选择 **Part 1 / 2 / 3**，再专项训练；通过独立的 Markdown 学习档案保存回答、跳过、复习目标与断点，支持新会话继续练习。

同时提供一个可上传的[单文件 prompt 版](dist/开始陪练.md)，用于不支持 skill 或本地文件操作的平台。

> **实验版。** 记录工具已有自动化测试；实时语音提醒、模型遵循流程及跨会话体验仍需按宿主验证。项目不是 IELTS 官方产品，不提供官方评分或提分保证。

## 功能

- **先选部分**：Part 1 日常问答、Part 2 任务卡陈述、Part 3 深入讨论；完整模考按需开启。
- **完整听完再点评**：英语出题，用户结束回答后点评表达、用词、语义、逻辑与语法，并可改进重答。
- **Part 2 准备计时**：工具实际计时一分钟，到期征求同意后才进入回答；需要宿主支持等待和语音提醒。
- **跨会话续接**：每个部分保留断点；跳过不算覆盖，但默认不会在新会话重新当作新题。
- **可检查的记录**：分别统计正式回答、跳过和用户手动通过；重复保存同一回答不重复计数。
- **轻量运行**：Python 标准库，无第三方依赖，不要求额外配置语音 API。

## 快速开始

### 方式一：使用 skill

宿主需要支持加载 skill、运行 Python、读写学习档案。实时语音由宿主提供，安装 skill 本身不会新增语音能力。

1. 下载或克隆仓库：

   ```sh
   git clone https://github.com/JasperStonnne/ielts-speaking-coach.git
   cd ielts-speaking-coach
   ```

2. 将 `skills/ielts-speaking-coach/` 整个目录导入宿主的 skill 目录。Codex 用户在 macOS / Linux 上可创建用户级链接：

   ```sh
   mkdir -p ~/.agents/skills
   ln -s "$PWD/skills/ielts-speaking-coach" ~/.agents/skills/ielts-speaking-coach
   ```

   保留克隆目录，符号链接依赖它。已有同名 skill 时先确认原安装位置，不重复创建。其他宿主按其 skill 导入方式配置。详见 [OpenAI 官方 skill 文档](https://developers.openai.com/codex/skills)。

3. 新开聊天，调用 skill：

   > 使用 $ielts-speaking-coach，开始今天的雅思口语练习。

4. 开启宿主的实时语音，选择 Part 1、2 或 3。结束说“今天到这里，保存进度”；下次调用同一 skill，选择部分后继续。

### 方式二：使用单文件 prompt

1. 下载 [`dist/开始陪练.md`](dist/开始陪练.md)，上传到 AI 聊天；不支持该附件格式时粘贴全文。
2. 开启语音，说“开始”，选择想练的部分。
3. 结束后保存 AI 生成的完整新版同名文件，下次提供新版继续。

这条路径需要手动保存；自动计时、音频访问与文件生成取决于宿主。模型不能运行工具时，不能声称已直接修改本地档案。

## 训练流程

| 部分 | 训练重点 | 专项流程 |
|---|---|---|
| Part 1 | 熟悉话题、直接回答、理由与例子 | 英文提问 → 完整回答 → 点评 → 可选重答 → 下一题 |
| Part 2 | 围绕任务卡连续陈述 | 英文任务卡 → 一分钟准备 → 确认开始 → 陈述 → 详细点评与重答 → 收尾 |
| Part 3 | 观点、原因、比较与论证 | 同主题深入问题 → 完整回答 → 点评 → 可选重答 → 下一题 |

用户要求“完整模拟考试”时按三个部分连续进行，结束后统一反馈。官方考试整体约 11–14 分钟；没有可靠的分段计时与语音控制时，只称为按结构模拟。参考 [IELTS 官方考试结构](https://ielts.org/take-a-test/test-types/ielts-academic-test/ielts-academic-format-speaking)。

只有转写文本时，不评价发音、真实停顿、语速或重音。用户请求估分时必须标明是非官方练习估计。

## 题库

| 内容 | 数量 |
|---|---:|
| Part 1 主问题 | 30 |
| Part 2 任务卡 | 10 |
| Part 3 主问题 | 50 |
| 主问题合计 | **90** |
| Part 2 收尾追问 | 20，不计主问题覆盖 |

全部为原创练习材料，包含 10 个主题及 Part 2 / Part 3 关联。不是官方完整题库、当季回忆题或预测题。约十轮使用不同任务卡后，Part 2 将进入复习。

维护源文件为 [`question-bank.json`](skills/ielts-speaking-coach/references/question-bank.json)。题号固定，不能复用旧编号表示另一道题。

## 学习记录

默认档案：`~/Documents/Codex/ielts-speaking-coach/learning-record.md`。也可设置 `IELTS_RECORD_PATH`，或通过工具的 `--record` 参数指定其他文件。

- **正式回答**：增加该题回答次数；同题重答不增加不同题目的覆盖数。
- **跳过**：保存处理记录，默认从后续题继续，不计正式回答或覆盖。
- **用户手动通过**：单独标记，不当作模型评分，也不伪造回答记录。

覆盖表示独立回答过哪些主问题，不等于掌握率。当前不判定“巩固”。

档案包含可读的 Markdown 视图和校验过的机读状态。工具提供回答去重、修订号与会话归属检查、文件锁、原子写入、前一版本备份和写后回读。损坏或未知格式会拒绝覆盖，保留原档案供检查。请勿直接编辑末尾的机读状态区。

个人档案独立于安装目录，更新 skill 不覆盖学习进度。旧单文件档案与工具档案尚不支持自动互相迁移。详见[记录协议](skills/ielts-speaking-coach/references/record-protocol.md)。

本项目不要求上传中央学习数据库；提供给云端 AI 的语音和文件仍由宿主处理。公开问题报告请使用虚构或脱敏记录，不提交真实档案、备份或秘密信息。

## 开发与验证

```sh
python3 -m unittest discover -s tests -v
python3 skills/ielts-speaking-coach/scripts/coach.py bank-check
python3 scripts/export_prompt.py
```

自动化测试覆盖跨会话跳过续接、三个专项模式、部分切换、重复保存、准备计时及确认、同题重答、写入冲突与损坏保护。测试只使用临时档案。CI 在多个 Python / 操作系统环境运行；具体结果以 Actions 为准。

- [软件验证记录](验证结果.md)
- [真实语音验证步骤](语音验证.md)

测试通过不代表所有语音平台都已兼容。欢迎提交包含宿主、版本、具体操作和实际结果的验证报告。

## 项目结构

```text
skills/ielts-speaking-coach/  # skill 入口、题库、流程与记录工具
scripts/export_prompt.py     # 从维护源文件生成单文件版
assets/                     # Logo 与生成说明
dist/                      # 单文件发行版
tests/                     # CLI 与档案回归测试
```

## 贡献

欢迎贡献原创题目、训练流程改进、平台验证和可复现的问题报告。修改前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。修改题库或流程后重新生成单文件版，并运行测试。

## 许可证

本仓库原创代码、指令、题库和图片按 [MIT License](LICENSE) 发布。外部链接材料及第三方品牌不属于本仓库许可范围。项目与 IELTS 官方机构无隶属或背书关系。
