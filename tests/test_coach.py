"""Behavioral tests of the actual CLI and saved Markdown state; no model/voice calls."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/ielts-speaking-coach/scripts/coach.py'
spec = importlib.util.spec_from_file_location('coach', SCRIPT)
coach = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coach)

class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.record = Path(self.tmp.name) / 'learning-record.md'
        self.rev = None
        self.client = 'chat-A'
        self.bank = coach.load_bank()

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, command, payload=None, extra=(), expected=None, client=None, success=True):
        argv = [sys.executable, str(SCRIPT), '--record', str(self.record), '--date', '2026-10-12', '--client', client or self.client]
        revision = self.rev if expected is None else expected
        if revision is not None: argv += ['--expected-revision', str(revision)]
        argv += [command, *extra]
        result = subprocess.run(argv, input=json.dumps(payload or {}), text=True, capture_output=True)
        data = json.loads(result.stdout)
        if success:
            self.assertEqual(result.returncode, 0, data)
            self.assertTrue(data['ok'])
            if 'revision' in data: self.rev = data['revision']
        else:
            self.assertEqual(result.returncode, 2, data)
            self.assertFalse(data['ok'])
        return data

    def start(self, mode='exam'):
        self.call('init')
        return self.call('start', extra=('--mode', mode))

    def answer(self, status, met=None):
        qid = status['session']['current_question']['id']
        payload = {'qid': qid, 'goal': '补充具体例子', 'met': met, 'independent': True,
                   'evidence': '有可理解的独立回答；这里是测试数据', 'action': '下次用一个具体例子解释'}
        return self.call('answer', payload, ('--event-id', status['session']['next_answer_id'])), payload

    def test_bank_and_initial_plan(self):
        self.assertEqual(self.call('bank-check')['parts'], {'1':30,'2':10,'3':50})
        s = self.start()
        plan = s['session']['plan']
        self.assertEqual([p['qid'] for p in plan[:9]], [f'P1-{n:03d}' for n in range(1,10)])
        self.assertEqual(plan[9]['qid'], 'P2-001')
        self.assertEqual(set(p['qid'] for p in plan[10:]), {'P3-001','P3-002','P3-021','P3-022','P3-023'})

    def test_cold_start_topic_preference(self):
        self.call('init')
        s = self.call('start', extra=('--mode','exam','--topic','旅行与交通'))
        self.assertEqual(s['session']['plan'][9]['qid'], 'P2-005')

    def test_cross_process_resume_from_preparation(self):
        s = self.start()
        for _ in range(9): s = self.call('skip')
        self.assertEqual(s['session']['stage'], 'preparation')
        self.call('pause')
        self.client = 'chat-B'
        s = self.call('resume', extra=('--takeover',))
        self.assertEqual(s['session']['id'], 'S0001')
        self.assertEqual(s['session']['current_question']['id'], 'P2-001')
        self.assertEqual(s['session']['stage'], 'preparation')
        self.assertEqual(s['coverage']['covered'], 0)

    def test_main_answer_and_closing_resume_count_once(self):
        s = self.start('part2')
        s = self.call('prepare', extra=('--confirmed',))
        s, _ = self.answer(s, False)
        self.assertEqual(s['session']['stage'], 'feedback')
        self.call('complete-turn')
        self.call('closing', {'note':'测试：完成第一道收尾'})
        self.call('pause')
        self.client = 'chat-B'
        s = self.call('resume', extra=('--takeover',))
        self.assertEqual(s['session']['closing_index'], 1)
        self.assertEqual(s['coverage']['covered'], 1)
        s = self.call('closing')
        self.assertEqual(s['session']['stage'], 'complete')
        self.call('finish', {'summary':'专项测试完成'})
        self.assertEqual(self.call('status')['coverage']['covered'], 1)
        state = coach.read_record(self.record, self.bank)
        self.assertEqual(state['progress']['P2-001']['count'], 1)
        self.assertEqual(state['progress']['P2-001']['review_due'], '2026-10-13')

    def test_idempotent_answer_with_stale_revision(self):
        s = self.start()
        old_revision = self.rev
        event_id = s['session']['next_answer_id']
        s, payload = self.answer(s)
        after = self.record.read_bytes()
        repeated = self.call('answer', payload, ('--event-id', event_id), expected=old_revision)
        self.assertTrue(repeated['idempotent'])
        self.assertEqual(self.record.read_bytes(), after)
        self.assertEqual(len(repeated['session']['answers']), 1)
        payload['evidence'] = '不同内容'
        error = self.call('answer', payload, ('--event-id',event_id), success=False)
        self.assertEqual(error['error'], 'event_conflict')

    def test_conflicting_revision_and_previous_owner_rejected(self):
        s = self.start()
        previous_revision = self.rev
        self.call('pause')
        after = self.record.read_bytes()
        error = self.call('resume', extra=('--takeover',), expected=previous_revision, client='chat-B', success=False)
        self.assertEqual(error['error'], 'revision_conflict')
        self.assertEqual(self.record.read_bytes(), after)
        self.call('resume', extra=('--takeover',), client='chat-B')
        error = self.call('skip', client='chat-A', success=False)
        self.assertEqual(error['error'], 'owner_conflict')

    def test_two_writers_same_revision(self):
        self.start()
        argv = [sys.executable,str(SCRIPT),'--record',str(self.record),'--expected-revision',str(self.rev),'--client',self.client,'skip']
        a = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        b = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        results = [json.loads(p.communicate()[0]) for p in (a,b)]
        self.assertEqual(sum(r['ok'] for r in results), 1)
        state = coach.read_record(self.record,self.bank)
        self.assertEqual(state['sessions'][0]['cursor'],1)

    def test_corruption_preserved_with_backup(self):
        self.start()
        backup = self.record.with_name(self.record.name+'.bak')
        before_backup = backup.read_bytes()
        text = self.record.read_text().replace('"revision": 1','"revision": 9')
        self.record.write_text(text)
        error = self.call('skip', success=False)
        self.assertEqual(error['error'],'record_corrupt')
        self.assertEqual(self.record.read_text(),text)
        self.assertEqual(backup.read_bytes(),before_backup)

    def test_finish_cannot_claim_incomplete_exam(self):
        self.start()
        error = self.call('finish', {'summary':'未完成'}, success=False)
        self.assertEqual(error['error'],'incomplete_session')
        self.assertEqual(self.call('status')['session']['status'],'active')

    def test_complete_exam_and_start_without_repeat(self):
        s = self.start()
        while s['session']['stage'] != 'complete':
            stage = s['session']['stage']
            if stage == 'preparation': s = self.call('prepare', extra=('--confirmed',))
            elif stage == 'closing': s = self.call('closing')
            else: s, _ = self.answer(s, True)
        self.assertEqual(s['coverage']['covered'],15)
        s = self.call('finish', {'summary':'完整状态机测试，不是语音测试'})
        s = self.call('start',extra=('--mode','exam'))
        self.assertEqual(s['session']['id'],'S0002')
        self.assertEqual(s['session']['plan'][9]['qid'],'P2-002')
        self.assertEqual(s['coverage']['covered'],15)

    def test_review_does_not_increase_counts(self):
        s = self.start()
        s, _ = self.answer(s)
        s = self.call('review', {'summary':'测试反馈','assessments':[{'event_id':'S0001-A01','met':False,'goal':'明确理由'}]})
        self.assertEqual(s['coverage']['covered'],1)
        state = coach.read_record(self.record,self.bank)
        self.assertEqual(state['progress']['P1-001']['count'],1)
        self.assertEqual(state['progress']['P1-001']['review_due'],'2026-10-13')

    def test_v0_migration_preserves_answers_and_backup(self):
        s = self.start()
        self.answer(s,True)
        state = coach.read_record(self.record,self.bank)
        state.pop('format_version')
        state['schema_version'] = 0
        self.record.write_text(coach.render(state,self.bank))
        old = self.record.read_bytes()
        migrated = self.call('migrate')
        self.assertEqual(migrated['format_version'],1)
        self.assertEqual(migrated['coverage']['covered'],1)
        self.assertEqual(self.record.with_name(self.record.name+'.bak').read_bytes(),old)
        self.assertEqual(coach.read_record(self.record,self.bank)['sessions'][0]['events'][0]['id'],'S0001-A01')

    def test_future_version_never_reset(self):
        self.start()
        state=coach.read_record(self.record,self.bank)
        state['format_version']=99
        self.record.write_text(coach.render(state,self.bank))
        before=self.record.read_bytes()
        error=self.call('init',success=False)
        self.assertEqual(error['error'],'migration_required')
        self.assertEqual(self.record.read_bytes(),before)

    def test_wrong_question_does_not_modify_file(self):
        s=self.start()
        before=self.record.read_bytes()
        payload={'qid':'P1-003','goal':'目标','met':None,'independent':True,'evidence':'测试','action':'动作'}
        error=self.call('answer',payload,('--event-id','S0001-A01'),success=False)
        self.assertEqual(error['error'],'question_conflict')
        self.assertEqual(self.record.read_bytes(),before)

    def test_repeat_same_question_increases_attempts_not_coverage(self):
        self.call('init')
        for _ in range(2):
            s=self.call('start',extra=('--mode','single','--question','P1-001'))
            self.answer(s,True)
            self.call('complete-turn')
            self.call('finish',{'summary':'用户指定重练的测试'})
        state=coach.read_record(self.record,self.bank)
        self.assertEqual(state['progress']['P1-001']['count'],2)
        self.assertEqual(self.call('status')['coverage']['covered'],1)

    def test_timer_elapsed_still_requires_explicit_confirmation(self):
        self.start('part2')
        s=self.call('timer-start',extra=('--seconds','1'))
        self.assertEqual(s['session']['preparation_timer']['phase'],'running')
        before=self.record.read_bytes()
        error=self.call('prepare',extra=('--confirmed',),success=False)
        self.assertEqual(error['error'],'timer_running')
        self.assertEqual(self.record.read_bytes(),before)
        s=self.call('timer-wait',extra=('--max-wait','2'))
        self.assertTrue(s['timer_ready'])
        self.assertEqual(s['session']['stage'],'preparation')
        error=self.call('prepare',success=False)
        self.assertEqual(error['error'],'confirmation_required')
        s=self.call('prepare',extra=('--confirmed',))
        self.assertEqual(s['session']['stage'],'speaking')
        self.assertEqual(s['coverage']['covered'],0)

    def test_timer_pause_and_resume_preserves_remaining(self):
        self.start('part2')
        self.call('timer-start')
        s=self.call('pause')
        remaining=s['session']['preparation_timer']['remaining_seconds']
        self.assertEqual(s['session']['preparation_timer']['phase'],'paused')
        self.client='chat-B'
        s=self.call('resume',extra=('--takeover',))
        self.assertEqual(s['session']['preparation_timer']['phase'],'running')
        self.assertAlmostEqual(s['session']['preparation_timer']['remaining_seconds'],remaining,delta=1)
        error=self.call('timer-start',success=False)
        self.assertEqual(error['error'],'timer_active')

    def test_coached_answer_feedback_repeat_preserves_one_question_coverage(self):
        s=self.start('part2')
        s=self.call('prepare',extra=('--confirmed',))
        s,_=self.answer(s,False)
        self.assertEqual(s['session']['stage'],'feedback')
        s=self.call('review',{'summary':'测试：给出具体改进点'})
        self.call('pause')
        self.client='chat-B'
        s=self.call('resume',extra=('--takeover',))
        self.assertEqual(s['session']['stage'],'feedback')
        s=self.call('redo')
        self.assertEqual(s['session']['stage'],'speaking')
        s,_=self.answer(s,True)
        self.assertEqual(s['session']['stage'],'feedback')
        self.assertEqual(s['coverage']['covered'],1)
        state=coach.read_record(self.record,self.bank)
        self.assertEqual(state['progress']['P2-001']['count'],2)
        self.assertEqual(state['sessions'][0]['events'][1]['id'],'S0001-A02')
        self.call('complete-turn')
        self.call('closing')
        self.call('closing')
        self.call('finish',{'summary':'两次回答一次覆盖'})

    def test_manual_pass_is_separate_from_answers_and_next_new_card(self):
        self.start('part2')
        s=self.call('mark',{'label':'用户手动通过','note':'用户确认，本轮没有正式回答'})
        self.assertEqual(s['session']['stage'],'complete')
        self.assertEqual(s['coverage']['covered'],0)
        self.assertEqual(s['session']['manual_marks'][0]['qid'],'P2-001')
        self.call('finish',{'summary':'用户手动确认，未实际作答'})
        s=self.call('start',extra=('--mode','part2'))
        self.assertEqual(s['session']['current_question']['id'],'P2-002')
        self.assertEqual(s['coverage']['covered'],0)

    def test_fresh_start_requires_part_selection_without_creating_session(self):
        self.call('init')
        before=self.record.read_bytes()
        error=self.call('start',success=False)
        self.assertEqual(error['error'],'part_selection_required')
        self.assertEqual(self.record.read_bytes(),before)

    def test_part1_and_part3_are_real_coached_modes(self):
        for mode,part in [('part1',1),('part3',3)]:
            s=self.start(mode)
            self.assertTrue(all(p['qid'].startswith(f'P{part}-') for p in s['session']['plan']))
            s,_=self.answer(s,True)
            self.assertEqual(s['session']['stage'],'feedback')
            s=self.call('redo')
            self.assertEqual(s['session']['stage'],'question')
            s,_=self.answer(s,True)
            self.assertEqual(s['coverage']['covered'],1 if part==1 else 2)
            s=self.call('complete-turn')
            while s['session']['stage']!='complete':s=self.call('skip')
            self.call('finish',{'summary':'专项模式测试'})

    def test_skipped_part2_continues_at_next_card_in_new_conversation(self):
        self.start('part2')
        s=self.call('skip')
        self.assertEqual(s['session']['stage'],'complete')
        self.call('pause')
        self.client='chat-B'
        s=self.call('continue',extra=('--takeover',))
        self.assertEqual(s['session']['current_question']['id'],'P2-002')
        self.assertEqual(s['coverage']['covered'],0)
        self.assertEqual(s['question_records'][0]['qid'],'P2-001')
        self.assertTrue(s['question_records'][0]['skipped'])
        self.assertEqual(s['answer_records_count'],0)

    def test_skips_are_not_reselected_after_session_finished(self):
        self.start('part2')
        self.call('skip')
        self.call('finish',{'summary':'跳过并结束'})
        s=self.call('start',extra=('--mode','part2'))
        self.assertEqual(s['session']['current_question']['id'],'P2-002')
        self.assertEqual(s['next_by_part']['2'],'P2-002')

    def test_part1_skip_and_pause_resumes_next_question(self):
        self.start('part1')
        self.call('skip')
        self.call('pause')
        self.client='chat-B'
        s=self.call('continue',extra=('--takeover',))
        self.assertEqual(s['session']['current_question']['id'],'P1-002')
        self.assertEqual(s['session']['id'],'S0001')

    def test_switch_parts_preserves_each_part_breakpoint(self):
        self.start('part1')
        self.call('skip')
        self.call('pause')
        self.client='chat-B'
        s=self.call('continue',extra=('--takeover','--mode','part3'))
        self.assertEqual(s['session']['current_question']['part'],3)
        self.assertEqual(s['parked_sessions'][0]['mode'],'part1')
        self.call('skip')
        self.client='chat-C'
        s=self.call('continue',extra=('--takeover','--mode','part1'))
        self.assertEqual(s['session']['current_question']['id'],'P1-002')
        self.assertEqual(s['session']['id'],'S0001')

    def test_explicit_single_question_can_revisit_skipped_question(self):
        self.start('part2')
        self.call('skip')
        self.call('finish',{'summary':'跳过'})
        s=self.call('start',extra=('--mode','single','--question','P2-001'))
        self.assertEqual(s['session']['current_question']['id'],'P2-001')

    def test_invalid_met_type_rejected(self):
        s=self.start()
        payload={'qid':'P1-001','goal':'目标','met':1,'independent':True,'evidence':'测试','action':'动作'}
        error=self.call('answer',payload,('--event-id','S0001-A01'),success=False)
        self.assertEqual(error['error'],'invalid_input')

if __name__ == '__main__':
    unittest.main()
