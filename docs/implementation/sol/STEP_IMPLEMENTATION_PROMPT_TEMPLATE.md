# OGC-SAGE 단계별 구현 프롬프트 템플릿

이 템플릿은 새 Codex 세션에서 OGC-SAGE 구현 단계를 하나씩 실행할 때 사용한다. 매 세션에서 아래 치환값만 지정하고, 한 단계가 끝나면 다음 단계를 자동으로 시작하지 않는다.

## 치환값

| 변수 | 설명 | 예시 |
|---|---|---|
| `[STEP]` | 이번에 구현할 단계 번호 | `1` |
| `[STEP_NAME]` | 짧은 단계명 | `foundation` |
| `[STEP_DOCUMENT]` | 단계 실행 문서 파일명 | `01_FOUNDATION_SAFE_INCUMBENT.md` |
| `[NEXT_STEP]` | 다음 단계 번호. 9단계는 `없음` | `2` |
| `[COMMIT_MESSAGE]` | 간단한 커밋 메시지 | `step 1: foundation` |

## 실행 프롬프트

```text
OGC-SAGE 구현의 [STEP]단계 “[STEP_NAME]”만 진행한다.

## 작업 위치

기존 worktree와 브랜치를 사용한다.

- worktree: /Users/brown/workspace/ogc/sol-native-implementation
- branch: sol-native-implementation
- remote: origin/sol-native-implementation

새 worktree나 브랜치를 만들지 않는다.

## 기준 문서

작업 전에 다음 문서를 처음부터 끝까지 읽는다.

1. docs/OGC2026_Competition_Algorithm_Design.md
2. docs/implementation/sol/OGC-SAGE_PROGRESS.md
3. docs/implementation/sol/[STEP_DOCUMENT]

설계 기준은 1번 문서이며, 이번 단계의 구체적인 범위, 인터페이스, 테스트와 완료 조건은 3번 문서를 따른다.

## 시작 전 확인

먼저 다음을 확인한다.

- 현재 branch와 upstream
- worktree 변경 상태
- 이전 단계의 완료 여부와 테스트 증거(1단계는 해당 없음)
- 이번 단계가 사용하는 이전 단계의 확정 인터페이스

기존 사용자 변경은 삭제하거나 덮어쓰지 않는다.

이전 단계가 완료되지 않았거나 저장소 상태가 안전하지 않으면 구현하지 말고 차단 사유를 보고한다.

## 구현 범위

- [STEP]단계 문서에 적힌 구현과 테스트만 수행한다.
- 문서의 세부 구현 순서대로 진행한다.
- 다음 단계의 기능을 미리 구현하지 않는다.
- `baseline/utils.py`와 `baseline/baseline_greedy.py`는 수정하지 않는다.
- 설계 문서에 없는 기능이나 리팩터링을 추가하지 않는다.
- 앞 단계의 확정 인터페이스를 임의로 변경하지 않는다.
- 설계와 현재 코드가 충돌하면 임의로 결정하지 말고 미해결 사항으로 기록한다.
- [NEXT_STEP]이 존재하더라도 이번 세션에서 시작하지 않는다.

다음 원칙을 유지한다.

- validated incumbent는 immutable하게 보존한다.
- candidate 변경은 transactional하게 처리한다.
- serialization은 canonical serializer만 사용한다.
- geometry의 최종 판정은 Shapely와 checker를 따른다.
- Gurobi 오류가 pure-Python fallback을 깨지 않게 한다.
- 시간 측정은 `time.monotonic()`을 사용한다.
- objective와 delta는 checker와 `1e-6` 상대 오차 이내여야 한다.

## 테스트

단계 문서에 명시된 테스트를 구현하고 실행한다.

실행 순서:

1. 이번 단계의 단위 테스트
2. 이번 단계의 통합 테스트
3. `baseline/tests` 전체 테스트
4. 단계 완료 조건 감사
5. 변경 파일과 금지 파일 확인

9단계가 아니라면 전체 40개 인스턴스 benchmark를 실행하지 않는다.

테스트가 실패하면:

- 현재 단계를 완료로 표시하지 않는다.
- 다음 단계로 진행하지 않는다.
- 재현 명령, 실제 결과와 원인을 기록한다.
- 실패 상태로 완료 커밋을 만들지 않는다.

## 진행 현황 업데이트

작업을 시작했다면 결과와 증거를 `docs/implementation/sol/OGC-SAGE_PROGRESS.md`에 갱신한다. 단, 구현과 테스트가 모두 완료되기 전에는 현재 단계를 완료로 표시하지 않는다.

다음을 기록한다.

- 구현 상태 또는 차단 상태
- 실행한 테스트 명령과 결과
- checker 및 objective 검증 결과
- 변경 파일
- 결정 사항과 미해결 사항
- 다음 단계 진입 가능 여부

## 커밋과 푸시

완료 조건이 모두 충족되면 이번 단계 변경만 커밋하고 push한다.

커밋 메시지:

[COMMIT_MESSAGE]

커밋에는 이번 단계의 구현, 테스트, 진행 현황 업데이트만 포함한다.

push 대상:

origin/sol-native-implementation

## 종료 보고

마지막에 다음을 보고한다.

- 구현한 내용
- 변경 파일
- 실행한 테스트와 결과
- checker/objective 검증 결과
- 진행 현황 문서 변경 내용
- 커밋 해시와 push 결과
- 미해결 사항
- 다음 단계 진입 가능 여부

[STEP]단계가 끝나면 중단하고 다음 단계를 자동으로 시작하지 않는다.
```

## 단계별 값

| STEP | STEP_NAME | STEP_DOCUMENT | NEXT_STEP | COMMIT_MESSAGE |
|---:|---|---|---:|---|
| 1 | `foundation` | `01_FOUNDATION_SAFE_INCUMBENT.md` | 2 | `step 1: foundation` |
| 2 | `geometry` | `02_GEOMETRY_FOUR_STATE.md` | 3 | `step 2: geometry` |
| 3 | `assignment` | `03_ASSIGNMENT_MASTER.md` | 4 | `step 3: assignment` |
| 4 | `constructor` | `04_EXACT_CONSTRUCTOR.md` | 5 | `step 4: constructor` |
| 5 | `retiming` | `05_EXACT_RETIMING.md` | 6 | `step 5: retiming` |
| 6 | `lns` | `06_HEURISTIC_LNS.md` | 7 | `step 6: lns` |
| 7 | `mip repair` | `07_CANDIDATE_SELECTION_MIP.md` | 8 | `step 7: mip repair` |
| 8 | `interlock` | `08_INTERLOCK_DENSIFIER.md` | 9 | `step 8: interlock` |
| 9 | `hardening` | `09_PACKAGING_STRESS_HARDENING.md` | 없음 | `step 9: hardening` |

## Codex에서 요청하는 방법

템플릿 파일을 첨부하거나 에디터에서 어노테이션한 뒤, 치환값을 별도 메시지로 전달한다. 템플릿 파일 자체를 매번 수정할 필요는 없다.

가장 짧은 요청 형식:

```text
첨부한 STEP_IMPLEMENTATION_PROMPT_TEMPLATE.md를 다음 값으로 실행해 주세요.

- STEP: 1
- STEP_NAME: foundation
- STEP_DOCUMENT: 01_FOUNDATION_SAFE_INCUMBENT.md
- NEXT_STEP: 2
- COMMIT_MESSAGE: step 1: foundation

템플릿의 범위와 중단 조건을 그대로 지켜 주세요.
```

파일 경로로 요청하는 형식:

```text
docs/implementation/sol/STEP_IMPLEMENTATION_PROMPT_TEMPLATE.md를 기준으로 OGC-SAGE 1단계를 실행해 주세요.

단계별 값 표의 1단계 값을 사용하고, 1단계 완료 후 커밋·푸시한 다음 중단하세요. 2단계는 시작하지 마세요.
```

## 큰 단계를 나누어 실행하는 방법

한 세션에서 최상위 단계 전체를 완료하기 어렵다면 단계 문서의 하위 작업 묶음만 지정한다.

```text
템플릿과 01_FOUNDATION_SAFE_INCUMBENT.md를 기준으로 1단계의 1A만 진행해 주세요.

- 1B 이후는 시작하지 마세요.
- 최상위 1단계를 완료로 표시하지 마세요.
- 1A 테스트 증거와 남은 작업만 진행 현황 문서에 기록하세요.
- 이번 세션에서는 커밋과 push를 하지 마세요.
```

하위 작업만 실행한 경우 최상위 단계 완료 커밋은 만들지 않는다. 이후 세션에서 남은 하위 작업과 전체 단계 테스트가 모두 끝난 뒤 단계 커밋을 만든다.
