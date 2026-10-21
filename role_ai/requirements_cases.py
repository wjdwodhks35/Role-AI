"""Small, synthetic reference set. Not a representative benchmark or a training dataset."""


def builtin_suite():
    def case(cid, request, goal, intents, tasks, constraints, missing, outputs, questions=None):
        questions = questions or []
        return {'id': cid, 'request': request, 'expected': {
            'goal': goal, 'intents': intents, 'tasks': tasks, 'constraints': constraints,
            'missing_inputs': missing, 'needs_clarification': bool(questions),
            'questions': questions, 'outputs': outputs}}
    return {'name': '기본 요구사항 분석 사례 v1 · 합성 10개', 'cases': [
        case('explain', 'Python 리스트와 튜플의 차이를 예시로 설명해 줘.',
             '리스트와 튜플의 차이 이해', ['explain'], ['explain'], [], [], ['explanation']),
        case('missing_code', '오류를 고치고 테스트 코드도 작성해 줘. 코드를 실행하지는 마.',
             '코드 오류 수정과 테스트 작성', ['fix_code', 'test_code'], ['ask', 'inspect', 'fix', 'test'],
             ['no_execution'], ['code'], ['patched_code', 'test_code'], ['수정할 코드를 제공해 주세요.']),
        case('write', 'Python으로 두 정수를 더하는 add 함수를 작성해 줘. 설명도 해 줘. 실행은 하지 마.',
             '덧셈 함수 작성과 설명', ['write_code', 'explain'], ['write', 'explain'],
             ['no_execution'], [], ['patched_code', 'explanation']),
        case('test_only', 'Python 코드 def add(a,b): return a+b 에 대한 unittest 코드를 작성해 줘. 실행하지 마.',
             '덧셈 함수의 테스트 작성', ['test_code'], ['test'], ['no_execution'], [], ['test_code']),
        case('execute_approval', 'Python 코드 print(1+1)을 실행하고 로그를 보여 줘. 외부 API는 쓰지 마.',
             '코드 실행과 로그 확인', ['execute_code'], ['ask', 'execute'], ['no_external_api'],
             ['approval'], ['execution_log'], ['별도 코드 실행 화면에서 이번 실행을 승인해 주세요.']),
        case('search_missing', '문서에서 회의 결정을 검색하고 근거를 보여 줘. 유료 호출은 하지 마.',
             '문서 근거 검색', ['search_documents'], ['ask', 'search'], ['no_paid_calls'],
             ['document'], ['sources'], ['검색할 문서를 선택하거나 제공해 주세요.']),
        case('train_missing', '로컬에서 모델을 학습하고 평가 보고서를 줘. 데이터 삭제는 하지 마. 데이터와 모델은 아직 정하지 않았어.',
             '로컬 학습과 평가', ['train_model'], ['ask', 'train'], ['local_only', 'no_data_deletion'],
             ['dataset', 'base_model', 'approval'], ['evaluation_report'],
             ['학습 데이터와 기반 모델을 지정하고 학습 승인 절차를 진행해 주세요.']),
        case('worker_missing', '그 Worker 작업을 취소하고 상태를 보고해 줘.',
             '지정 작업 취소와 상태 확인', ['manage_workers'], ['ask', 'manage'], [],
             ['target'], ['status_report'], ['취소할 Worker 작업 ID를 알려 주세요.']),
        case('backup', '현재 Role-AI 데이터를 새 경로 /backup/new-snapshot에 백업해 줘. API와 모든 Worker는 중지했어. 기존 데이터는 보존하고 결과를 보고해 줘.',
             '기존 데이터 보존 백업', ['backup_restore'], ['backup'], ['preserve_existing_data'], [],
             ['backup_bundle', 'status_report']),
        case('conflict', '코드를 지금 실행해 줘. 하지만 절대로 실행하지 마. 코드도 아직 안 줬어.',
             '충돌하는 실행 요구 확인', ['execute_code'], ['ask'], ['no_execution'],
             ['code', 'approval'], ['plan'], ['실행 여부에 대한 상충된 지시를 먼저 명확히 해 주세요.']),
    ]}
