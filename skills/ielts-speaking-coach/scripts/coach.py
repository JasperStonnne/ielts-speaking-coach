#!/usr/bin/env python3
"""IELTS practice state machine. Standard library only; JSON stdin for user text."""
import argparse
import contextlib
import copy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
BANK_PATH = ROOT / 'references' / 'question-bank.json'
START = '<!-- IELTS_RECORD_STATE_START -->'
END = '<!-- IELTS_RECORD_STATE_END -->'
FORMAT = 1

class CoachError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)

def fail(code, message):
    raise CoachError(code, message)

def valid_met(value):
    return value is None or isinstance(value, bool)

def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def timer_view(session):
    timer = session.get('preparation_timer')
    if not timer:
        return None
    if timer.get('paused_remaining') is not None:
        remaining = timer['paused_remaining']
        phase = 'paused'
    else:
        remaining = max(0, math.ceil((datetime.fromisoformat(timer['ends_at']) - datetime.now(timezone.utc)).total_seconds()))
        phase = 'running' if remaining else 'awaiting_confirmation'
    return {'duration_seconds': timer['duration_seconds'], 'remaining_seconds': remaining, 'phase': phase}

def canonical(state):
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def digest(state):
    return hashlib.sha256(canonical(state).encode()).hexdigest()

def load_bank():
    bank = json.loads(BANK_PATH.read_text(encoding='utf-8'))
    if bank.get('schema_version') != 1:
        fail('bank_version', 'Unsupported question bank schema.')
    ids = [q['id'] for q in bank['questions']]
    if len(ids) != len(set(ids)):
        fail('bank_invalid', 'Duplicate question IDs.')
    for q in bank['questions']:
        if not re.fullmatch(r'P[123]-\d{3}', q['id']) or q['part'] != int(q['id'][1]):
            fail('bank_invalid', 'Invalid question ID or Part.')
        if q['part'] == 2:
            if len(q.get('points', [])) != 3 or len(q.get('closing', [])) != 2:
                fail('bank_invalid', 'Incomplete cue card.')
            linked = [x for x in bank['questions'] if x['id'] in q['related']]
            if len(linked) != len(q['related']) or any(x['part'] != 3 or x['topic'] != q['topic'] for x in linked):
                fail('bank_invalid', 'Broken discussion links.')
    return bank

def blank(bank):
    return {'format_version': FORMAT, 'revision': 0, 'bank_version': bank['version'],
            'created_at': stamp(), 'updated_at': stamp(), 'last_session_id': None,
            'owner_client': None, 'sessions': [], 'progress': {}}

def current(state):
    active = [s for s in state['sessions'] if s['status'] in ('active', 'paused')]
    if len(active) > 1:
        fail('record_invalid', 'More than one unfinished session.')
    return active[0] if active else None

def recompute(state):
    progress = {}
    for s in state['sessions']:
        for e in s['events']:
            p = progress.setdefault(e['qid'], {'count': 0})
            p['count'] += 1
            p.update(last_date=e['date'], goal=e['goal'], state='待复习' if e['met'] is False else '练过')
            p['review_due'] = (date.fromisoformat(e['date']) + timedelta(days=1 if e['met'] is False else 3)).isoformat() if e['met'] is not None else None
    state['progress'] = progress

def question_records(state):
    records = {}
    for qid, p in state['progress'].items():
        records[qid] = {'qid': qid, 'answer_count': p['count'], 'last_answer_date': p['last_date'],
                        'skipped': False, 'manual_pass': False}
    for s in state['sessions']:
        for qid in s.get('skipped', []):
            item = records.setdefault(qid, {'qid': qid, 'answer_count': 0, 'last_answer_date': None, 'skipped': False, 'manual_pass': False})
            item['skipped'] = True
        for m in s.get('manual_marks', []):
            item = records.setdefault(m['qid'], {'qid': m['qid'], 'answer_count': 0, 'last_answer_date': None, 'skipped': False, 'manual_pass': False})
            item['manual_pass'] = True
    return [records[qid] for qid in sorted(records)]

def validate_state(state, bank, allow_old=False):
    if state.get('format_version') != FORMAT:
        if allow_old and state.get('schema_version') == 0:
            return
        fail('migration_required', 'Unsupported record version. Preserve the file; use a documented migration, never reset it.')
    if not isinstance(state.get('revision'), int) or state['revision'] < 0:
        fail('record_invalid', 'Invalid record revision.')
    ids = {q['id'] for q in bank['questions']}
    seen_sessions, seen_events = set(), set()
    for s in state.get('sessions', []):
        if s['id'] in seen_sessions or not re.fullmatch(r'S\d{4,}', s['id']):
            fail('record_invalid', 'Duplicate or invalid session ID.')
        seen_sessions.add(s['id'])
        if s['status'] not in ('active', 'paused', 'parked', 'finished') or not 0 <= s['cursor'] <= len(s['plan']):
            fail('record_invalid', 'Invalid session status or cursor.')
        plan_ids = [p['qid'] for p in s['plan']]
        if len(plan_ids) != len(set(plan_ids)) or not set(plan_ids) <= ids:
            fail('record_invalid', 'Invalid session plan; bank changes need explicit migration.')
        for e in s['events']:
            if e['id'] in seen_events or not re.fullmatch(re.escape(s['id']) + r'-A\d{2,}', e['id']):
                fail('record_invalid', 'Duplicate or invalid answer ID.')
            seen_events.add(e['id'])
            if e['qid'] not in plan_ids or not valid_met(e['met']) or not isinstance(e['independent'], bool):
                fail('record_invalid', 'Invalid answer event.')
            date.fromisoformat(e['date'])
        if not set(s.get('skipped', [])) <= set(plan_ids):
            fail('record_invalid', 'Unknown skipped question.')
        if s['cursor'] < len(s['plan']):
            qid = s['plan'][s['cursor']]['qid']
            allowed = ('preparation', 'speaking', 'feedback', 'closing') if qid.startswith('P2-') else ('question', 'feedback')
            if s['stage'] not in allowed:
                fail('record_invalid', 'Invalid stage for current Part.')
            if s['stage'] in ('feedback', 'closing') and not any(e['qid'] == qid for e in s['events']):
                fail('record_invalid', 'Closing stage requires a completed long turn.')
        elif s['stage'] != 'complete':
            fail('record_invalid', 'Completed plan must have complete stage.')
    current(state)
    reconstructed = copy.deepcopy(state)
    recompute(reconstructed)
    if reconstructed['progress'] != state.get('progress'):
        fail('record_invalid', 'Progress does not agree with answer events.')
    if state['bank_version'] != bank['version']:
        fail('bank_migration_required', 'Question bank version changed; verify IDs before migrating.')

def read_record(path, bank, allow_old=False):
    text = path.read_text(encoding='utf-8')
    if text.count(START) != 1 or text.count(END) != 1:
        fail('record_corrupt', 'Missing or duplicate state block. Keep this file and its .bak backup.')
    block = text.split(START)[1].split(END)[0]
    m = re.search(r'```json\s*\n(.*?)\n```', block, re.S)
    if not m:
        fail('record_corrupt', 'Unreadable state block.')
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        fail('record_corrupt', 'Invalid JSON state. Do not overwrite the file.')
    checksum = re.search(r'<!-- SHA256:([0-9a-f]{64}) -->', text)
    if not checksum or checksum.group(1) != digest(state):
        fail('record_corrupt', 'Checksum mismatch. Preserve the original and inspect the .bak backup.')
    validate_state(state, bank, allow_old)
    return state

def cell(value):
    return str(value or '—').replace('|', '\\|').replace('\n', ' ')

def render(state, bank):
    counts = {part: sum(q['part'] == part for q in bank['questions']) for part in (1, 2, 3)}
    lines = ['# 雅思口语学习档案', '', '此文件由记录工具维护。表格供阅读，末尾数据区为唯一机器状态；请通过工具修改。', '',
             f"格式版本：{state.get('format_version', state.get('schema_version'))}", f"档案修订：{state['revision']}",
             f"题库版本：{state['bank_version']}", f"最近轮次：{state['last_session_id'] or '尚未开始'}", '',
             '## 覆盖统计', '', '| Part | 主问题数 | 已覆盖 | 覆盖率 | 待复习 |', '|---|---:|---:|---:|---:|']
    for part in (1, 2, 3):
        ps = [p for qid, p in state['progress'].items() if qid.startswith(f'P{part}-')]
        lines.append(f"| Part {part} | {counts[part]} | {len(ps)} | {len(ps)/counts[part]:.1%} | {sum(p['state']=='待复习' for p in ps)} |")
    lines += ['', '## 当前断点', '']
    s = current(state)
    if s:
        qid = s['plan'][s['cursor']]['qid'] if s['cursor'] < len(s['plan']) else '全部已答，待复盘结束'
        lines += [f"- 轮次：{s['id']}（{s['status']}）", f"- 模式：{s['mode']}", f'- 当前题：{qid}',
                  f"- 环节：{s['stage']}", f"- 收尾追问已完成：{s['closing_index']}",
                  '- 剩余题：' + '、'.join(p['qid'] for p in s['plan'][s['cursor']:])]
    else:
        lines.append('尚无进行中的练习；说“开始”自动安排下一轮。')
    lines += ['', '## 题目进度', '', '| 题号 | 状态 | 次数 | 最近日期 | 当前目标 | 下次复习 |', '|---|---|---:|---|---|---|']
    for qid, p in sorted(state['progress'].items()):
        lines.append('| ' + ' | '.join(cell(x) for x in (qid, p['state'], p['count'], p['last_date'], p['goal'], p['review_due'])) + ' |')
    lines += ['', '## 题库处理记录（与实际覆盖分开）', '', '| 题号 | 实际回答次数 | 曾跳过 | 用户手动通过 |', '|---|---:|---|---|']
    for item in question_records(state):
        lines.append(f"| {item['qid']} | {item['answer_count']} | {'是' if item['skipped'] else '否'} | {'是' if item['manual_pass'] else '否'} |")
    lines += ['', '## 最近三轮', '']
    for session in state['sessions'][-3:]:
        lines += [f"### {session['id']} · {session['date']} · {session['status']}", '', cell(session.get('summary')), '',
                  '| 回答 | 题号 | 独立 | 具体目标 | 达标 | 依据与行动 |', '|---|---|---|---|---|---|']
        for e in session['events']:
            met = '待复盘' if e['met'] is None else ('是' if e['met'] else '否')
            lines.append('| ' + ' | '.join(cell(x) for x in (e['id'], e['qid'], '是' if e['independent'] else '有提示', e['goal'], met, e['evidence'] + '；' + e['action'])) + ' |')
        for mark in session.get('manual_marks', []):
            lines.append(f"\n用户手动标记：{mark['qid']} · {cell(mark['label'])}；{cell(mark['note'])}（不计实际作答次数）。")
    lines += ['', '## 工具状态（保留完整历史）', '', f'<!-- SHA256:{digest(state)} -->', START, '```json',
              json.dumps(state, ensure_ascii=False, indent=2), '```', END, '']
    return '\n'.join(lines)

@contextlib.contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_name(path.name + '.lock')
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    stream = os.fdopen(fd, 'r+b')
    try:
        if os.name == 'nt':
            import msvcrt
            if not stream.read(1):
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                fail('busy', 'Another writer is saving. Read status and retry later.')
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                fail('busy', 'Another writer is saving. Read status and retry later.')
        yield
    finally:
        stream.close()

def atomic_write(path, data):
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def save(path, state, bank):
    validate_state(state, bank)
    if path.exists():
        atomic_write(path.with_name(path.name + '.bak'), path.read_text(encoding='utf-8'))
    atomic_write(path, render(state, bank))
    if read_record(path, bank) != state:
        fail('verification_failed', 'Saved record did not match. Inspect record and backup.')

def summary(state, bank):
    ids = {q['id']: q for q in bank['questions']}
    s = current(state)
    total = len(ids)
    result = {'format_version': state['format_version'], 'revision': state['revision'],
              'bank_version': bank['version'], 'last_session_id': state['last_session_id'],
              'owner_client': state['owner_client'], 'coverage': {'covered': len(state['progress']), 'total': total,
              'percentage': round(len(state['progress']) / total * 100, 1),
              'review': sum(p['state'] == '待复习' for p in state['progress'].values())}, 'session': None,
              'recent_summaries': [{'id': x['id'], 'summary': x['summary']} for x in state['sessions'][-3:]]}
    result['question_records'] = question_records(state)
    result['answer_records_count'] = sum(len(x['events']) for x in state['sessions'])
    result['parked_sessions'] = [{'id': x['id'], 'mode': x['mode'], 'stage': x['stage']} for x in state['sessions'] if x['status'] == 'parked']
    result['available_parts'] = [1, 2, 3]
    result['next_by_part'] = {}
    for part in (1, 2, 3):
        try:
            result['next_by_part'][str(part)] = plan_for(state, bank, date.max, f'part{part}')[0]['qid']
        except CoachError:
            result['next_by_part'][str(part)] = None
    if s:
        q = ids[s['plan'][s['cursor']]['qid']] if s['cursor'] < len(s['plan']) else None
        next_number = max([int(e['id'].split('-A')[1]) for e in s['events']] + [0]) + 1
        result['session'] = {'id': s['id'], 'status': s['status'], 'mode': s['mode'], 'stage': s['stage'],
                             'cursor': s['cursor'], 'plan': s['plan'], 'closing_index': s['closing_index'],
                             'current_question': q, 'next_answer_id': f"{s['id']}-A{next_number:02d}",
                             'answers': s['events'], 'interrupted': s['interrupted'],
                             'manual_marks': s.get('manual_marks', []),
                             'preparation_timer': timer_view(s),
                             'previous_focus': state['progress'].get(q['id']) if q else None}
        if q and s['stage'] == 'closing':
            result['session']['closing_question'] = q['closing'][s['closing_index']]
    return result

def require_day(value):
    if not value: fail('date_required', 'Pass --date YYYY-MM-DD using the learner local date.')
    return date.fromisoformat(value)

def plan_for(state, bank, day, mode='exam', topic=None, question=None):
    all_questions = bank['questions']
    manual = {m['qid']: m for session in state['sessions'] for m in session.get('manual_marks', [])}
    skipped = set()
    for session in state['sessions']:
        skipped.update(session.get('skipped', []))
        skipped.difference_update(e['qid'] for e in session['events'])
        skipped.difference_update(m['qid'] for m in session.get('manual_marks', []))
    if mode == 'single':
        matched = next((q for q in all_questions if q['id'] == question), None)
        if not matched: fail('unknown_question', 'Single-question mode requires a valid --question ID.')
        return [{'qid': question, 'reason': '用户指定专项或重练'}]
    if question: fail('invalid_input', '--question requires --mode single.')
    qs = [q for q in all_questions if q['id'] not in skipped]
    if topic and topic not in {q['topic'] for q in all_questions}:
        fail('unknown_topic', 'Topic must match the bank.')
    def rank(q):
        p = state['progress'].get(q['id']) or ({'last_date': manual[q['id']]['date'], 'review_due': None} if q['id'] in manual else None)
        if topic and q['topic'] == topic: prefix = 0
        elif topic: prefix = 1
        else: prefix = 0
        if not p: priority = 0
        elif p['review_due'] and p['review_due'] <= day.isoformat(): priority = 1
        else: priority = 2
        return (prefix, priority, (p.get('review_due') or p['last_date']) if p else '', q['id'])
    def reason(q):
        p = state['progress'].get(q['id'])
        if not p and q['id'] in manual: return '用户手动标记过；没有实际作答记录，其他新题不足时复习'
        if not p: return '新题'
        if p['review_due'] and p['review_due'] <= day.isoformat(): return '到期复习'
        return '本部分或主题新题不足，复习最久未练题'
    def group(part, size, match_topic=False):
        pool = sorted((q for q in qs if q['part'] == part and (not topic or q['topic'] == topic)), key=rank)
        if not pool: fail('part_exhausted', 'This Part or topic has no available questions. Explicit single-question practice can revisit a skipped ID.')
        if match_topic:
            pool = [q for q in pool if q['topic'] == pool[0]['topic']]
        return pool[:size]
    if mode == 'part1': selected = group(1, 3, True)
    elif mode == 'part3': selected = group(3, 5, True)
    elif mode == 'part2': selected = group(2, 1)
    elif mode == 'exam':
        p2 = group(2, 1)[0]
        p1 = sorted((q for q in qs if q['part'] == 1), key=rank)[:9]
        p3 = sorted((q for q in qs if q['id'] in p2['related']), key=rank)[:5]
        selected = p1 + [p2] + p3
    else: fail('part_selection_required', 'Choose Part 1, 2 or 3 before starting. Full exam is opt-in.')
    return [{'qid': q['id'], 'reason': reason(q)} for q in selected]

def start_session(state, bank, day, mode, topic=None, question=None):
    sid = f"S{max([int(x['id'][1:]) for x in state['sessions']] + [0]) + 1:04d}"
    plan = plan_for(state, bank, day, mode, topic, question)
    session = {'id': sid, 'date': day.isoformat(), 'mode': mode, 'plan': plan, 'cursor': 0,
               'stage': 'preparation' if plan[0]['qid'].startswith('P2-') else 'question', 'closing_index': 0,
               'events': [], 'skipped': [], 'closing_notes': [], 'status': 'active', 'interrupted': False, 'summary': ''}
    state['sessions'].append(session)
    state['last_session_id'] = sid
    return session

def freeze_timer(session):
    timer = session.get('preparation_timer')
    if timer and session['stage'] == 'preparation':
        timer['paused_remaining'] = timer_view(session)['remaining_seconds']

def restore_timer(session):
    timer = session.get('preparation_timer')
    if timer and timer.get('paused_remaining') is not None:
        timer['ends_at'] = (datetime.now(timezone.utc) + timedelta(seconds=timer['paused_remaining'])).isoformat()
        timer['paused_remaining'] = None

def next_question(s):
    s['cursor'] += 1
    s['closing_index'] = 0
    s['preparation_timer'] = None
    if s['cursor'] == len(s['plan']): s['stage'] = 'complete'
    else: s['stage'] = 'preparation' if s['plan'][s['cursor']]['qid'].startswith('P2-') else 'question'

def payload_from_stdin():
    try:
        data = json.loads(__import__('sys').stdin.read() or '{}')
    except json.JSONDecodeError:
        fail('invalid_input', 'Pass one JSON object on stdin.')
    if not isinstance(data, dict): fail('invalid_input', 'JSON input must be an object.')
    return data

def answer_fields(payload):
    allowed = {'qid', 'goal', 'met', 'independent', 'evidence', 'action'}
    if set(payload) - allowed: fail('invalid_input', 'Unexpected answer fields.')
    for key in ('qid', 'goal', 'evidence', 'action'):
        if not isinstance(payload.get(key), str): fail('invalid_input', f'{key} must be text.')
        if len(payload[key]) > 1200: fail('invalid_input', 'Keep evidence and goals compact.')
    if not valid_met(payload.get('met')) or not isinstance(payload.get('independent'), bool):
        fail('invalid_input', 'met is boolean or null; independent is boolean.')
    return {key: payload.get(key) for key in sorted(allowed)}

def execute(args, bank):
    path = Path(args.record).expanduser().resolve()
    if path == ROOT or ROOT in path.parents:
        fail('record_location', 'Keep personal records outside the installed skill directory.')
    if args.command == 'bank-check':
        return {'ok': True, 'bank_version': bank['version'], 'questions': len(bank['questions']), 'parts': {str(p): sum(q['part']==p for q in bank['questions']) for p in (1,2,3)}}
    if args.command in ('status', 'validate'):
        if not path.exists():
            return {'ok': True, 'record': str(path), 'needs_init': True}
        state = read_record(path, bank)
        return {'ok': True, 'record': str(path), **summary(state, bank)}
    if args.command == 'timer-wait':
        if not 1 <= args.max_wait <= 45:
            fail('invalid_input', 'Wait at most 45 seconds per call.')
        limit = time.monotonic() + args.max_wait
        while True:
            state = read_record(path, bank)
            session = current(state)
            if not session or session['stage'] != 'preparation' or state['owner_client'] != args.client or session['status'] != 'active':
                return {'ok': True, 'timer_cancelled': True}
            timer = timer_view(session)
            if not timer:
                fail('timer_missing', 'Start the preparation timer first.')
            if timer['phase'] == 'awaiting_confirmation' or time.monotonic() >= limit:
                return {'ok': True, 'timer_ready': timer['phase'] == 'awaiting_confirmation', **summary(state, bank)}
            time.sleep(min(0.25, max(0, limit - time.monotonic())))
    payload = payload_from_stdin() if args.command in ('answer', 'review', 'finish', 'closing', 'mark') else {}
    with locked(path):
        if args.command == 'init':
            if path.exists():
                state = read_record(path, bank)
                return {'ok': True, 'record': str(path), 'already_initialized': True, **summary(state, bank)}
            state = blank(bank)
            save(path, state, bank)
            return {'ok': True, 'record': str(path), 'saved': True, **summary(state, bank)}
        if not path.exists(): fail('record_missing', 'Initialize this record before starting.')
        state = read_record(path, bank, allow_old=args.command == 'migrate')
        # A retried answer can safely succeed even if its original revision is stale.
        # Ownership and the original payload must still match; it never writes again.
        if args.command == 'answer' and state.get('format_version') == FORMAT:
            fields = answer_fields(payload)
            s = current(state)
            if s and args.client == state['owner_client']:
                found = next((e for e in s['events'] if e['id'] == args.event_id), None)
                if found:
                    request_hash = hashlib.sha256(canonical(fields).encode()).hexdigest()
                    if found['request_hash'] != request_hash:
                        fail('event_conflict', 'Answer ID already contains different content.')
                    return {'ok': True, 'idempotent': True, **summary(state, bank)}
        if args.expected_revision is None or args.expected_revision != state['revision']:
            fail('revision_conflict', 'Record changed or expected revision missing. Read status; do not blindly retry.')
        if args.command == 'migrate':
            if state.get('format_version') == FORMAT:
                return {'ok': True, 'already_current': True, **summary(state, bank)}
            state.pop('schema_version', None)
            state['format_version'] = FORMAT
            recompute(state)
        else:
            s = current(state)
            if args.command == 'start':
                require_day(args.date)
                if s: fail('unfinished_session', 'Use resume to continue the existing session.')
                owner = args.client or str(uuid.uuid4())
                s = start_session(state, bank, require_day(args.date), args.mode, args.topic, args.question)
                state['owner_client'] = owner
            elif args.command == 'continue':
                day = require_day(args.date)
                owner = args.client or str(uuid.uuid4())
                if s and state['owner_client'] != owner and not args.takeover:
                    fail('owner_conflict', 'Continuing in a new conversation requires explicit takeover.')
                mode = args.mode or (s['mode'] if s else (state['sessions'][-1]['mode'] if state['sessions'] else None))
                if not mode: fail('part_selection_required', 'Choose Part 1, 2 or 3 first.')
                if s and s['stage'] == 'complete':
                    s['status'] = 'finished'
                    s = None
                if s and s['mode'] != mode:
                    freeze_timer(s)
                    s['status'] = 'parked'
                    s = None
                if not s:
                    s = next((x for x in reversed(state['sessions']) if x['status'] == 'parked' and x['mode'] == mode), None)
                    if not s:
                        question = None
                        if mode == 'single':
                            last = next(x for x in reversed(state['sessions']) if x['mode'] == 'single')
                            question = last['plan'][0]['qid']
                        s = start_session(state, bank, day, mode, question=question)
                s['status'] = 'active'
                restore_timer(s)
                state['owner_client'] = owner
                state['last_session_id'] = s['id']
            elif args.command == 'resume':
                if not s: fail('no_session', 'No unfinished session; start a new one.')
                resume_client = args.client or str(uuid.uuid4())
                if state['owner_client'] != resume_client and not args.takeover:
                    fail('owner_conflict', 'Another conversation owns this record. Explicit takeover is required to resume here.')
                state['owner_client'] = resume_client
                s['status'] = 'active'
                restore_timer(s)
            else:
                if not s: fail('no_session', 'No unfinished session.')
                if not args.client or args.client != state['owner_client']:
                    fail('owner_conflict', 'This conversation does not own the record. Read status before taking over.')
                if s['status'] != 'active': fail('paused', 'Resume this session before changing it.')
                if args.command == 'pause':
                    freeze_timer(s)
                    s['status'], s['interrupted'] = 'paused', True
                elif args.command == 'timer-start':
                    if s['stage'] != 'preparation': fail('stage_conflict', 'Not in preparation.')
                    if not 1 <= args.seconds <= 120: fail('invalid_input', 'Timer duration must be 1–120 seconds; normal IELTS preparation is 60.')
                    if s.get('preparation_timer') and not args.restart:
                        fail('timer_active', 'A timer exists. Preserve it unless the learner explicitly requests restart.')
                    now = datetime.now(timezone.utc)
                    s['preparation_timer'] = {'duration_seconds': args.seconds, 'started_at': now.isoformat(),
                                              'ends_at': (now + timedelta(seconds=args.seconds)).isoformat(), 'paused_remaining': None}
                elif args.command == 'prepare':
                    if s['stage'] != 'preparation': fail('stage_conflict', 'Not in Part 2 preparation.')
                    if not args.confirmed: fail('confirmation_required', 'The learner must explicitly agree before speaking starts.')
                    timer = timer_view(s)
                    if timer and timer['remaining_seconds'] > 0 and not args.early:
                        fail('timer_running', 'Preparation time is not finished. Early start requires explicit learner request.')
                    s['stage'] = 'speaking'
                    s['preparation_confirmed_at'] = stamp()
                elif args.command == 'answer':
                    fields = answer_fields(payload)
                    request_hash = hashlib.sha256(canonical(fields).encode()).hexdigest()
                    found = next((e for e in s['events'] if e['id'] == args.event_id), None)
                    if found:
                        if found['request_hash'] != request_hash: fail('event_conflict', 'Answer ID already contains different content.')
                        return {'ok': True, 'idempotent': True, **summary(state, bank)}
                    if s['stage'] not in ('question', 'speaking'): fail('stage_conflict', 'Current stage is not accepting a main answer.')
                    qid = s['plan'][s['cursor']]['qid']
                    if fields['qid'] != qid: fail('question_conflict', 'Answer does not match the current question.')
                    expected_id = f"{s['id']}-A{max([int(e['id'].split('-A')[1]) for e in s['events']] + [0]) + 1:02d}"
                    if args.event_id != expected_id: fail('event_conflict', 'Use the next answer ID from status.')
                    s['events'].append({'id': args.event_id, 'date': require_day(args.date).isoformat(), **fields, 'request_hash': request_hash})
                    if qid.startswith('P2-'):
                        s['stage'] = 'feedback' if s['mode'] in ('part2', 'single') else 'closing'
                    elif s['mode'] in ('part1', 'part3', 'single'):
                        s['stage'] = 'feedback'
                    else: next_question(s)
                elif args.command == 'redo':
                    if s['stage'] != 'feedback': fail('stage_conflict', 'Only a completed coached long turn can be repeated after feedback.')
                    s['stage'] = 'speaking' if s['plan'][s['cursor']]['qid'].startswith('P2-') else 'question'
                elif args.command == 'complete-turn':
                    if s['stage'] != 'feedback': fail('stage_conflict', 'Finish the coached long turn from its feedback stage.')
                    if s['plan'][s['cursor']]['qid'].startswith('P2-'): s['stage'] = 'closing'
                    else: next_question(s)
                elif args.command == 'closing':
                    if s['stage'] != 'closing': fail('stage_conflict', 'Not in cue-card closing questions.')
                    note = payload.get('note', '')
                    if not isinstance(note, str) or len(note) > 1200: fail('invalid_input', 'Closing note must be compact text.')
                    s['closing_notes'].append({'qid': s['plan'][s['cursor']]['qid'], 'index': s['closing_index'], 'note': note})
                    s['closing_index'] += 1
                    if s['closing_index'] == 2: next_question(s)
                elif args.command == 'skip':
                    if s['stage'] == 'complete': fail('stage_conflict', 'No remaining question.')
                    if s['stage'] not in ('closing', 'feedback'): s['skipped'].append(s['plan'][s['cursor']]['qid'])
                    next_question(s)
                elif args.command == 'mark':
                    if s['stage'] == 'complete': fail('stage_conflict', 'No current question to mark.')
                    if payload.get('label') != '用户手动通过' or not isinstance(payload.get('note'), str):
                        fail('invalid_input', 'Only an explicit learner manual pass annotation is supported.')
                    qid = s['plan'][s['cursor']]['qid']
                    s.setdefault('manual_marks', []).append({'qid': qid, 'label': payload['label'],
                        'note': payload['note'], 'date': require_day(args.date).isoformat()})
                    next_question(s)
                elif args.command in ('review', 'finish'):
                    if args.command == 'finish' and s['stage'] != 'complete':
                        fail('incomplete_session', 'There are remaining questions. Pause rather than claiming completion.')
                    if set(payload) - {'summary', 'assessments'}: fail('invalid_input', 'Unknown review fields.')
                    if not isinstance(payload.get('summary', ''), str): fail('invalid_input', 'summary must be text.')
                    assessments = payload.get('assessments', [])
                    if not isinstance(assessments, list): fail('invalid_input', 'assessments must be a list.')
                    if len({a['event_id'] for a in assessments}) != len(assessments): fail('invalid_input', 'Duplicate assessment IDs.')
                    events = {e['id']: e for e in s['events']}
                    for a in assessments:
                        if set(a) - {'event_id', 'goal', 'met', 'evidence', 'action'}: fail('invalid_input', 'Unknown assessment fields.')
                        if a['event_id'] not in events: fail('invalid_input', 'Assessment must reference this session.')
                        e = events[a['event_id']]
                        for key in ('goal', 'evidence', 'action'):
                            if key in a:
                                if not isinstance(a[key], str) or len(a[key]) > 1200: fail('invalid_input', 'Keep assessments compact.')
                                e[key] = a[key]
                        if 'met' in a:
                            if not valid_met(a['met']): fail('invalid_input', 'met must be boolean or null.')
                            e['met'] = a['met']
                    s['summary'] = payload.get('summary', s['summary'])
                    if args.command == 'finish':
                        s['status'] = 'finished'
                        state['owner_client'] = None
                else:
                    fail('unknown_command', 'Unknown command.')
        recompute(state)
        state['revision'] += 1
        state['updated_at'] = stamp()
        save(path, state, bank)
        return {'ok': True, 'record': str(path), 'saved': True, **summary(state, bank)}

def main():
    default = os.environ.get('IELTS_RECORD_PATH') or str(Path.home() / 'Documents' / 'Codex' / 'ielts-speaking-coach' / 'learning-record.md')
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--record', default=default)
    p.add_argument('--expected-revision', type=int)
    p.add_argument('--client')
    p.add_argument('--date')
    subs = p.add_subparsers(dest='command', required=True)
    for name in ('init','status','validate','bank-check','pause','closing','skip','review','finish','migrate','redo','complete-turn','mark'):
        subs.add_parser(name)
    prepare = subs.add_parser('prepare')
    prepare.add_argument('--confirmed', action='store_true')
    prepare.add_argument('--early', action='store_true')
    timer_start = subs.add_parser('timer-start')
    timer_start.add_argument('--seconds', type=int, default=60)
    timer_start.add_argument('--restart', action='store_true')
    timer_wait = subs.add_parser('timer-wait')
    timer_wait.add_argument('--max-wait', type=int, default=45)
    start = subs.add_parser('start')
    start.add_argument('--mode', choices=('exam','part1','part2','part3','single'))
    start.add_argument('--topic')
    start.add_argument('--question')
    resume = subs.add_parser('resume')
    resume.add_argument('--takeover', action='store_true')
    cont = subs.add_parser('continue')
    cont.add_argument('--takeover', action='store_true')
    cont.add_argument('--mode', choices=('exam','part1','part2','part3','single'))
    answer = subs.add_parser('answer')
    answer.add_argument('--event-id', required=True)
    args = p.parse_args()
    try:
        result = execute(args, load_bank())
    except CoachError as e:
        result = {'ok': False, 'error': e.code, 'message': e.message}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        result = {'ok': False, 'error': 'invalid_record_or_input', 'message': str(e)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ok'] else 2)

if __name__ == '__main__':
    main()
