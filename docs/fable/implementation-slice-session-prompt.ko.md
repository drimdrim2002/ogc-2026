# 재사용 가능한 구현 슬라이스 세션 프롬프트

<!--
@prompt-name fable-implementation-slice
@purpose 새 Codex 세션에서 계획된 구현 슬라이스를 정확히 하나 실행합니다.

@param slice_id
  required: true
  type: string
  pattern: ^S[0-6]-[0-9]{2}$
  example: S0-01
  meaning: 단계 문서에서 실행할 정확한 슬라이스 제목입니다.

@param stage_doc
  required: true
  type: path
  example: docs/fable/implementation-steps/s0-foundation.md
  meaning: 권위 있는 단계 계획의 절대 경로 또는 대상 워크트리 기준 상대 경로입니다.

@derived commit_message
  source: 선택한 슬라이스 안의 `Commit:` 항목입니다.
  fallback: 해당 항목이 없으면, 완료된 diff에서 간결한 Conventional Commit 메시지를 도출합니다.
  user_input_required: false

@context-authority master=docs/fable/fable-native-implementation-progress.md
@context-authority reset=docs/fable/implementation-steps/plan-reset-01.md
@protected-context legacy_worktree=/Users/brown/workspace/ogc/fable-native-implementation
@fixed-context interpreter=/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python

@stop-condition 선택한 슬라이스를 커밋하고 푸시한 뒤 또는 차단 사유를 기록한 뒤 중지합니다.
@scope-rule 동일 세션에서 다른 슬라이스나 전체 단계 게이트를 절대 시작하지 않습니다.
-->

## 호출 방법

새 세션에서 다음 지시문과 두 매개변수만 전송합니다.

```text
Follow docs/fable/implementation-slice-session-prompt.md.

slice_id: S0-01
stage_doc: docs/fable/implementation-steps/s0-foundation.md
```

두 예시 값을 바꾸십시오. 커밋 메시지 매개변수나 붙여 넣은 계획 텍스트는 필요하지 않습니다.

## 재사용 가능한 프롬프트

당신은 OGC 2026 저장소에서 계획된 구현 슬라이스 하나를 구현하고 있습니다.

사용자가 제공하는 런타임 매개변수:

- `slice_id = {{slice_id}}`
- `stage_doc = {{stage_doc}}`

이 플레이스홀더를 호출 메시지에서 제공한 값으로 취급합니다. 누락된 값은 거부합니다. 마스터 진행 문서와 PLAN-RESET-01에서 대상 워크트리와 브랜치를 결정한 뒤, 상대 `stage_doc`을 그 대상 워크트리 기준으로 정규화합니다. 문서가 존재하고 `slice_id`에 해당하는 제목을 정확히 하나 포함하는지 확인합니다. 저장소 계획에 이미 명시된 정보는 사용자에게 묻지 않습니다.

### 실행 컨텍스트와 보존 경계

- 워크트리/브랜치: 마스터와 PLAN-RESET-01에 기록된 전용 깨끗한 대상
- 보존 전용 레거시 워크트리: `/Users/brown/workspace/ogc/fable-native-implementation`의 `fable-native-implementation`
- Python: `/opt/homebrew/Caskroom/miniforge/base/envs/ogc-2026/bin/python`
- 마스터 진행 문서: `docs/fable/fable-native-implementation-progress.md`
- 체커 권한 기준: `baseline/utils.py`
- 동결된 참조: `baseline/baseline_greedy.py`

모든 Python 명령에는 명시된 Python 인터프리터를 사용합니다. 체커나 동결된 참조는 절대 수정하지 않습니다.

PLAN-RESET-01이 레거시 워크트리를 dirty 보존 원본으로 표시하는 동안 그곳에서 구현 슬라이스를 실행하지 않습니다. 그 워크트리에서 branch switch, reset, restore, stash, stage, commit, clean, push를 하지 않습니다. 구현 재개 전에 보존 manifest와 별도 안정화 워크트리가 존재해야 합니다.

### 1. 선택한 슬라이스 확인

다음 순서대로 완전히 읽습니다.

1. `docs/fable/fable-native-implementation-progress.md`
2. `docs/fable/implementation-steps/plan-reset-01.md`
3. 제공된 `stage_doc`
4. 선택한 `slice_id` 섹션 및 해당 단계 수준의 의존성, 게이트, 정리, 롤백, 증거, 위험 규칙
5. 해당 슬라이스를 실행하는 데 필요한 소스 파일만

사용자에게 묻지 않고 선택한 슬라이스에서 다음을 추출합니다.

- 선행 조건 및 소비하는 아티팩트
- 포함·제외 범위
- 정확한 파일 및 심볼
- 의도한 RED 사유
- 최소 구현 동작
- 목표 GREEN, 회귀, 체커, 벤치마크, 패리티 또는 스트레스 명령
- 증거 경로 및 PASS/FAIL 기준
- 정리 및 롤백 동작
- 제안된 커밋 메시지

단계 문서는 실행 계약입니다. 슬라이스별 텍스트가 상위 우선순위 체커 동작과 충돌하면, 임의로 새 설계를 선택하지 말고 중지하여 불일치를 기록합니다.

### 2. 사전 점검

편집 전 다음을 수행합니다.

1. 워크트리 경로, 브랜치, HEAD, 업스트림 및 `git status --short --branch`를 확인하고, reset이 지정한 전용 대상이며 보존 전용 레거시 워크트리가 아님을 입증합니다.
2. 마스터 문서와 Git 이력에서 선택한 슬라이스의 선행 슬라이스 및 단계 선행 조건을 확인합니다.
3. 필수인 이전 게이트가 모두 통과로 기록되어 있는지 확인합니다. 필수 S6-05/06은 깨끗한 S3 baseline을 요구하며 S4/S5 완료를 요구하지 않습니다. 선택 슬라이스는 자체 게이트로 이미 승격된 하위 계층만 소비합니다.
4. 프로젝트 인터프리터가 존재하는지 확인합니다.
5. 구현 전에 대상 워크트리가 깨끗해야 합니다. 관련 없는 사용자 변경은 보존하고 절대 스테이징하지 않습니다. 변경이 선택한 슬라이스와 겹치거나 정확한 경로 allowlist를 방해하면 중지하고 차단 사유를 보고합니다.
6. 슬라이스에 필요한 읽기 전용 탐색을 수행합니다.
7. 실행할 모든 명령을 개발(D), 안전/사전 qualification(S), 동결 qualification(Q)으로 분류하고 실행 전에 기록합니다.
8. 구현을 시작하기 전에 규정된 대로 마스터 상태/이력을 업데이트합니다.

필수 선행 조건이 누락되면 시작하지 않습니다. 선택 사항인 이후 단계 기능의 부재는 절대로 이를 조기에 추가할 이유가 되지 않습니다.

### 3. 실패 우선으로 실행

선택한 슬라이스만 수행합니다.

1. 지정된 동작 테스트를 먼저 추가합니다.
2. 정확한 대상 테스트를 실행하고 의도한 동작상의 이유로 RED가 발생하는지 확인합니다.
3. 구문 오류, 깨진 픽스처, 잘못된 import 경로, 관련 없는 의존성 누락 또는 테스트 탐색 실패는 유효한 RED가 아닙니다. 테스트 설정을 수정하고, RED가 선택한 동작의 누락 또는 부정확성을 나타낼 때까지 다시 실행합니다.
4. 진행 이력에 정확한 RED 명령, 종료 코드, 의도한 실패 및 증거 경로를 기록합니다.
5. 슬라이스에 필요한 최소 동작을 구현합니다. 다른 슬라이스에 할당된 편의 작업은 구현하지 않습니다.
6. 대상 테스트를 실행하여 GREEN이 되는지 확인합니다.
7. 슬라이스에 지정된 회귀 테스트를 실행합니다.
8. 지정된 실제 또는 합성 인스턴스에서 공식 체커 검증을 실행합니다.
9. 필요한 경우 단계 계획이 지정한 tier에서 벤치마크, 패리티, A/B 또는 스트레스 명령을 실행합니다.
10. 계획된 저장소 로컬 경로에 구조화된 증거를 저장합니다.

진행하기 위해 테스트를 건너뛰거나, 약화하거나, 삭제하거나, `xfail`로 표시하지 않습니다. 공식 체커를 내부 목표값 또는 실행 가능성 계산으로 절대 대체하지 않습니다.

개발 RED/GREEN과 affected regression은 관련 소스 또는 테스트 변경 후 반복할 수 있습니다. 안전/사전 qualification 검사도 관련 변경 후 반복할 수 있습니다. 모든 시도는 새 identity를 사용하고 실패 증거를 보존합니다. Exact-once는 소스 커밋, 깨끗한 워크트리, 구성, selector, instance, seed, 명령, 임계값을 qualification manifest에 동결한 뒤 Tier Q에만 적용합니다. Q 명령은 해당 불변 identity에서 한 번만 실행하며, 실패하면 그 identity의 남은 Q 체인을 중지합니다. 수정은 Tier D로 돌아가 새 identity를 만들며, 실패한 identity를 그대로 재실행하지 않습니다.

### 4. 슬라이스 결정

다음 슬라이스 수준 기준을 모두 통과한 경우에만 슬라이스를 완료로 표시합니다.

- 의도한 RED가 입증됨
- 대상 GREEN 통과
- 필수 회귀 테스트 통과
- 공식 체커 검증 통과
- 필수 구조화된 증거가 완전함
- 정리 완료
- 저장소가 제출 가능한 상태로 유지됨

안전성, 체커 패리티, 실행 가능성, 기존 해의 무결성, 타임아웃, 프로세스 정리 또는 필수 증거가 실패하면 다음을 수행합니다.

1. 기준을 약화하지 않습니다.
2. 다음 슬라이스를 시작하지 않습니다.
3. 계획된 롤백을 적용하거나, 규정된 대로 마지막으로 검증된 기존 해를 보존합니다.
4. 정확한 차단 사유와 증거를 마스터 상태/이력에 업데이트합니다.
5. 부분 동작에 대해 성공 커밋을 만들지 않고 중지합니다.

선택 기능은 단계 계획이 허용하는 경우 `GATE_FAILED_DISABLED`로 끝날 수 있습니다. PLAN-RESET-01에 따라 선택 트랙의 `BLOCKED` 또는 `GATE_FAILED_DISABLED`는 마지막으로 검증된 하위 계층의 필수 하드닝을 막지 않습니다.

### 5. 자율적으로 커밋 및 푸시

슬라이스가 성공한 후 다음을 수행합니다.

1. RED, GREEN, 체커 검증, 측정 및 최종 슬라이스 결정 후에 마스터 진행 문서를 업데이트합니다.
2. 필요에 따라 자식 프로세스, 솔버 환경, 임시 파일, 생성된 패키지 및 불완전한 증거를 정리합니다.
3. 전체 diff를 검사하고 변경된 모든 파일이 선택한 슬라이스에 속하는지 확인합니다. 정확한 경로 allowlist를 생성하고 추가 경로가 하나라도 있으면 스테이징 전에 거부합니다.
4. 커밋 메시지를 자동으로 도출합니다.
   - 먼저 선택한 슬라이스의 `Commit:` 값을 사용합니다.
   - 값이 없으면 완료한 슬라이스만 설명하는 간결한 Conventional Commit 메시지를 만듭니다.
5. allowlist에 포함된 선택 슬라이스 파일만 스테이징합니다. 스테이징 전 diff와 staged diff 해시를 기록하고 working diff와 cached diff를 따로 검토합니다.
6. 원자적인 구현 커밋을 정확히 하나 만듭니다.
7. 검증된 현재 브랜치를 구성된 업스트림에 푸시합니다. 업스트림이 없으면 `git push -u origin <verified-current-branch>`를 사용합니다.
8. 로컬 HEAD가 업스트림 HEAD와 같고 워크트리가 깨끗한지 확인합니다.

사용자가 별도로 요청하지 않는 한 풀 리퀘스트를 만들지 않습니다.

### 6. 중지 경계

선택한 슬라이스를 성공적으로 커밋하고 푸시한 직후 또는 차단 사유가 완전히 기록된 직후에 중지합니다. 다음을 하지 않습니다.

- 다음 슬라이스 시작
- 선택한 슬라이스가 명시적으로 해당 게이트인 경우를 제외하고 전체 단계 게이트 실행 또는 실행했다고 주장
- 관련 없는 코드를 기회주의적으로 리팩터링
- 계획 상태만으로 구현 단계 완료를 주장

### 7. 최종 응답

다음을 간결하게 보고합니다.

1. 선택한 슬라이스와 결과(`COMPLETE` 또는 `BLOCKED`)
2. 구현된 관찰 가능한 동작
3. RED, GREEN, 회귀, 체커 및 측정 결과
4. 구조화된 증거 경로
5. 변경 파일 요약
6. 성공한 경우 커밋 SHA 및 푸시 결과
7. 정확한 최종 `git status --short --branch`
8. 방향 안내 목적으로만 명시하며 시작하지 않은, 다음으로 실행 가능한 슬라이스

차단된 경우, 커밋/푸시 세부 정보 대신 정확한 차단 사유, 롤백 상태 및 필요한 다음 조치를 제시합니다.

## 추가 호출 예시

```text
Follow docs/fable/implementation-slice-session-prompt.md.

slice_id: S1-03
stage_doc: docs/fable/implementation-steps/s1-constructor.md
```

```text
Follow docs/fable/implementation-slice-session-prompt.md.

slice_id: S4-02
stage_doc: docs/fable/implementation-steps/s4-assignment-refinement.md
```
