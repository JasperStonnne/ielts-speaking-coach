# 记录工具协议 · 格式版本 1

运行时工具：同目录 skill 下的 `scripts/coach.py`。Python 3.9+，仅标准库，不联网。平台不能执行脚本时使用单文件版，不能假装工具已执行。

## 调用约定

所有全局选项放在子命令之前：

```text
python3 <skill>/scripts/coach.py [--record <绝对路径>] [--expected-revision N] [--client C] [--date YYYY-MM-DD] <子命令> [子命令选项]
```

`--date` 必须使用用户当地日期。start 和 answer 需要日期。默认个人档案为 `~/Documents/Codex/ielts-speaking-coach/learning-record.md`；也可设置 `IELTS_RECORD_PATH` 或显式传 `--record`。路径在所有聊天中保持一致，不把个人记录放在可更新的 skill 目录中。

返回 JSON。`ok: false` 或退出码 2 表示未成功。保存成功时工具已经写后回读，返回 `saved: true` 和新 revision。不要把“已经调用工具”当作“已经保存”。

| 子命令 | 输入与作用 |
|---|---|
| status | 只读：返回各类题目记录、正式回答条数、各 Part 下一题、覆盖、当前题、阶段、next_answer_id、owner_client、revision 与最近简短复盘；不存在则 needs_init |
| init | 创建空白档案；存在则读取，绝不重置 |
| start | 需要 revision、当地日期；可省 client 由工具生成。必须显式选择 --mode part1/part2/part3；完整模考用 exam；可 `--mode part2`、`--topic <主题>` 或 `--mode single --question P1-001` |
| continue | 需要 revision、当地日期；新会话 --takeover，可显式 --mode part1/part2/part3；当前题组已结束就自动选下一组，换 Part 保留另一部分断点 |
| resume | 需要 revision；新会话加 `--takeover`，client 可由工具生成。沿用原轮，不新增轮次 |
| timer-start | 仅 preparation；默认真实 60 秒，保存截止时间。用户明确重置才加 --restart |
| timer-wait | 只读等待，--max-wait 最多 45 秒；返回 timer_ready 后仍停在准备阶段，不自动进入陈述 |
| prepare | 必须 --confirmed 表示用户明确同意；计时未结束仅用户要求提前开始才加 --early |
| redo | 专项 feedback 阶段，同意重答后回到 speaking，保留原题与第一次记录 |
| complete-turn | 专项 feedback 后结束辅导，进入收尾 |
| answer | `--event-id <next_answer_id>`，stdin JSON；保存一条主回答。相同 ID 与相同原始内容重试不会重复计数 |
| closing | stdin 可有 `{"note":"简短记录"}`；只推进一个收尾，不增加主回答次数 |
| mark | 用户明确要求时，stdin label=“用户手动通过”、note=事实说明；推进当前题，不增加真实作答次数或覆盖 |
| skip | 跳过未完成主问题；收尾阶段则结束剩余收尾 |
| review | stdin summary 与 assessments；补充反馈，不结束轮次、不增加次数 |
| pause | 中途停止，准确保留环节；准备计时冻结剩余秒数 |
| finish | 完成所有候选题或显式跳过后复盘结束；未完成时拒绝，避免虚构完整模考 |
| validate / bank-check | 检查档案完整性或题号、任务卡及关联 |
| migrate | 仅支持已知的内部 schema_version=0 同结构档案升到 1；先备份，保留历史。未来版本拒绝，旧通用 prompt 文本不自动转换 |

首次 start/resume 返回的 owner_client 是这一个聊天的身份，后续命令必须沿用。新聊天继续时显式接管；旧聊天之后写入会被拒绝。修订冲突要重新读状态并处理，不能盲目重复旧写入。

## answer 输入

通过 stdin 传 JSON，或先将 JSON 保存到工作临时文件，再以输入重定向传入。不要把用户句子插入 shell 参数、命令替换或反引号。

```json
{
  "qid": "P1-001",
  "goal": "用具体例子展开观点",
  "met": null,
  "independent": true,
  "evidence": "确有依据的短片段或表现描述",
  "action": "结束复盘时确定下一步"
}
```

met 仅 true、false 或 null；independent 是布尔值。存在提示则 false。不知道原句时写“无可确认原句”，不编造引语。记录证据应简短，不把完整转写存入工具。

## review / finish 输入

```json
{
  "summary": "一个优点与两个优先改进点",
  "assessments": [
    {
      "event_id": "S0001-A01",
      "goal": "用具体例子展开观点",
      "met": false,
      "evidence": "本次回答有观点，但未提供具体例子",
      "action": "下次加一个真实经历"
    }
  ]
}
```

只更新本轮已有回答，不新增虚构回答。部分结束用 review 后 pause；整轮完成才 finish。覆盖与次数始终从唯一回答事件核算。

## 文件与损坏处理

Markdown 表格供人阅读，末尾校验过的 JSON 数据区为机器状态；不要手工改 JSON 或让 AI 整份重写。写入有文件锁、预期 revision 检查、临时文件原子替换和回读核对。`.bak` 保留上一个成功版本，不作为另一个活动档案。`last_session_id` 是审计字段，单靠它不能防止并发覆盖。

未知格式、校验失败、题库版本改变时拒绝写入。保留原文件和备份，报告具体情况，不自动 reset。格式迁移与题库升级分开处理；当前题库升级需先确认旧题 ID/内容对应，未实现自动题库迁移。

工具只输出当前轮与最近三轮的短复盘，不把完整历史加载进模型；完整历史仍保存在档案数据区。首次创建的个人档案为空，软件测试在临时档案中进行，不污染真实记录。

## 跨会话跳过与专项入口

跳过不增加回答次数，但参与所有后续选题。已有跳过记录无需清空或手动迁移；工具会读取旧 session.skipped 并排除。status 的 question_records 汇总回答、曾跳过与手动通过，不能只根据覆盖率判断是否有记录。continue 在已完成/已跳过题组之后自动选后续题；切换 Part 保存未完成部分为 parked，选回可恢复。档案格式版本不变，字段兼容旧档案。
