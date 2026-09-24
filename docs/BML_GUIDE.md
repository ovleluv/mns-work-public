# BML guide

Part 1 is the authoring checklist for BML-lite JSON that the current engine executes (Korean).
Part 2 is the detailed execution reference: conditions, timing, phases, directives and task
semantics. Supported scope and limits are in [MISSION/MISSION_FEASIBILITY.md](../MISSION/MISSION_FEASIBILITY.md).

## Part 1 — Authoring checklist

_BML 생성 가이드 — v49.11_

이 문서는 현재 엔진에서 **실행되는 BML-lite JSON**을 만들기 위한 작성 순서와 검증 기준이다. 작전 표현의 상세 예시는 [2부](#part-2--execution-reference), 지원 범위와 제약은 [MISSION/MISSION_FEASIBILITY.md](../MISSION/MISSION_FEASIBILITY.md)를 참고한다. BML은 명령 의도를 전달하며 센서, 전투, 지형, 교리의 물리 규칙을 대신하지 않는다.

### 1. 생성 전에 시나리오를 확인한다

1. 시나리오의 `world.width_m`, `world.height_m`, 부대 ID·소속·초기 위치, `objectives`, 지형의 건물·교량 ID를 읽는다. 좌표 단위는 미터이며 명령 좌표는 지도 안에 있어야 한다.
2. `aggregations`가 있으면 **활성 집계 부모 ID**를 명령한다. 집계된 자식은 비활성 상태이므로 BML 명령 대상이 될 수 없다.
3. BLUE와 RED 계획을 각각 독립된 파일로 작성하고 최상위 `side`를 맞춘다. 보통 `replace_existing_orders: true`를 명시한다. 이 값은 BML에 등장한 부대의 기존 명령만 교체하며, 등장하지 않은 부대는 그대로 둔다. `false`는 기존 큐 뒤에 추가한다.
4. 실재하지 않는 부대·건물 ID, 지원하지 않는 task·directive·조건 경로를 만들지 않는다. 로더는 분기 안쪽까지 검사하고 오류가 있으면 기존 명령 큐를 교체하지 않는다.

### 2. 권장 형식과 실행 예시

새 계획에는 `missions` 또는 `phases`를 사용한다. 아래 예시의 `B-INF-2`와 좌표는 `scenarios/demo.json`에 맞춘 것이다.

```json
{
  "side": "BLUE",
  "replace_existing_orders": true,
  "phases": [
    {
      "id": "APPROACH",
      "missions": [
        {
          "id": "B-INF-2-APPROACH",
          "unit": "B-INF-2",
          "task": "MOVE_TO",
          "destination": [1500, 1850]
        }
      ]
    },
    {
      "id": "ATTACK",
      "start_at_s": 120,
      "missions": [
        {
          "id": "B-INF-2-ATTACK",
          "unit": "B-INF-2",
          "task": "ATTACK_POSITION",
          "destination": [2000, 1850],
          "persistent": false,
          "deadline_s": 300,
          "conditions": [
            {"lhs": "self.loss_ratio", "op": ">=", "rhs": 0.4}
          ],
          "on_true": {
            "id": "B-INF-2-LOSS-WITHDRAW",
            "task": "WITHDRAW",
            "destination": [1200, 1800]
          },
          "on_deadline": {
            "id": "B-INF-2-LATE-WITHDRAW",
            "task": "WITHDRAW",
            "destination": [1200, 1800]
          },
          "directives": {
            "engagement_range_policy": "COMBINED_ARMS",
            "engagement_range_fraction": 0.9
          }
        }
      ]
    }
  ]
}
```

`phases`는 **부대별 명령 큐**로 컴파일된다. 첫 임무가 끝난 부대만 다음 임무로 진행하고, 다른 부대의 완료를 기다리는 전역 장벽은 없다. `start_at_s`는 시뮬레이션 시작 후 절대 초 단위의 시작 제한이다. 시작 전에도 현지 교리 반응이나 자동 교전은 일어날 수 있다.

### 3. 지원 task 선택

| 목적 | task와 주요 필드 | 실행 의미 |
|---|---|---|
| 좌표 기동 | `MOVE_TO`: `destination` | 목적지 도착으로 완료 |
| 좌표 공격 | `ATTACK_POSITION`: `destination`, 선택 `persistent` | 이동 중 관측된 적과 교전. 기본 `persistent: true`는 목적지 도착만으로 완료되지 않음 |
| 특정 적 추적 | `ATTACK_UNIT`, `DESTROY_UNIT`: `target`, 선택 `target_position` | 적 ID만으로 실제 위치를 알지 못함. Track 또는 BML에 명시한 정적 정보 위치로 이동 |
| 지역 방어 | `DEFEND_POSITION`, `DEFEND_AREA`, `SECURE_AREA`: `center`/`radius_m` 또는 `polygon` | 방어·교전. `SECURE_AREA`는 제한된 추격과 중심 복귀를 기본 사용 |
| 거점 공격 | `SEIZE`: `destination` 또는 시나리오 `objective` ID | 좌표 공격으로 컴파일. 점령 성공 판정까지 제공하지 않음 |
| 대기·철수 | `HOLD`: 선택 `duration_s`; `WITHDRAW`: `destination` | `HOLD`의 기본 기간은 무기한. `WITHDRAW`는 집결지 도착으로 완료되며 접촉 단절을 뜻하지 않음 |
| 수송 | `DISMOUNT`, `MOUNT`, `BOARD`(`carrier`), `DISEMBARK` | 생존 승무원, 좌석, 소속, 집결 거리·시간 등 실제 조건을 확인 |
| 건물·시설 | `ENTER_BUILDING`, `EXIT_BUILDING`, `ATTACK_STRUCTURE`, `STRIKE_INFRASTRUCTURE` | 지도에 정의된 구조물 ID 사용. 건물 진입·이탈은 FOOT 부대, 건물 공격은 구조물 효과가 있는 직사화기, 시설 간접 타격은 포병과 `targets` 목록이 필요 |
| 장애물 | `BUILD_BARRICADE`: `position`, 선택 `heading_deg` | 할당된 `barricade_limit`이 있는 적격 보병이 건설 |

새 파일에는 `task` 형식을 권장한다. 과거 `orders_by_unit`의 엔진 `kind` 형식도 읽지만, 지원되지 않는 `kind`는 로드 단계에서 거부된다. `missions`와 `phases`를 한 파일에 함께 넣으면 평면 `missions`가 먼저, 단계 임무가 뒤에 큐에 들어간다.

### 4. 조건·기한·분기

- 조건 형식은 `{"lhs": "self.loss_ratio", "op": ">=", "rhs": 0.4}`이다. 연산자는 `<`, `<=`, `>`, `>=`, `==`, `!=`이며 `conditions` 배열의 여러 조건은 **AND**다.
- `on_true`는 활성 명령을 매 시뮬레이션 단계에서 검사해 조건이 참이면 그 명령으로 **교체**한다. 완료 시에는 조건이 참이면 `on_true`, 거짓이면 `on_false`를 큐 앞에 추가한다. 완료 분기는 해당 명령의 목표·경과 시간 정보가 지워지기 전에 평가된다.
- `deadline_s`(별칭 `complete_by_s`)는 절대 초 단위다. 기한을 넘기면 로그를 남기며, `on_deadline`이 있을 때만 분기한다. 속도를 높이거나 위치를 순간이동시키지 않는다. 같은 단계에서 `on_true`와 기한이 동시에 성립하면 조건 분기가 먼저 적용된다.
- 지원 `lhs`: `sim.time`, `self.loss_ratio`, `self.strength_ratio`, `self.personnel`, `self.initial_personnel`, `self.equipment`, `self.initial_equipment`, `self.state`, `self.time_in_order`, `self.capability.<CAPABILITY>`, `self.distance_to_objective`, `self.at_objective`, `self.enemy_count_near`.
- `self.enemy_count_near`는 오래된 이름을 유지하지만 **행동 가능한 Track의 추정 위치**를 센다. 기본 반경은 800m이고 부대 메타데이터 `condition_radius_m`로 바꿀 수 있다. 이 값은 실제 적의 위치·생존이나 지휘부의 확정 정보를 뜻하지 않는다.

분기 안에도 유효한 task·필수 필드·지도 안 좌표를 써야 한다. 무기한 `HOLD`나 기본 `persistent: true`인 `ATTACK_POSITION` 뒤에 후속 임무를 단순히 추가하면 그 임무가 시작되지 않을 수 있다.

### 5. 지원 directive와 인식 정보

`directives`에서 허용되는 키는 `hold_at_all_costs`, `allow_withdrawal`, `allow_break_contact`, `allow_artillery_displacement`, `allow_indirect_fire_dispersion`, `assault`(불리언, `false`면 돌격 없이 지원사격만 수행), `engagement_range_policy`(`STANDOFF`/`BALANCED`/`COMBINED_ARMS` 등), `engagement_range_fraction`(양의 수)이다. 이 값은 현지 반응·교전 거리 정책을 조정한다. 예를 들어 `hold_at_all_costs`는 물리적 능력을 되살리거나 사격을 금지하지 않는다. `hold_fire: true`처럼 지원되지 않는 키는 로드 오류다.

`ATTACK_UNIT`/`DESTROY_UNIT`의 `target_position`은 지휘관이 제공한 **고정된 추정 좌표**다. 엔진이 실제 적 위치를 채워 주지 않는다. 표적의 실제 소멸도 자동으로 명령을 완료시키지 않는다. 현재는 명시적 종말 증거/BDA를 자동 생성하지 않으므로, 그런 완료를 전제로 후속 명령을 설계하지 않는다. 적 ID가 없거나 불확실하면 `ATTACK_POSITION`을 사용한다.

### 6. 검증 순서

프로젝트 루트에서 먼저 JSON 문법을 확인하고, 같은 시나리오·BML 조합을 headless 로더로 검사한다. 다음은 bash/zsh 예시다.

```bash
python -m json.tool blue_bml.json > /dev/null
python - <<'PY'
from mnsim.scenario import load_scenario

sim = load_scenario("scenarios/demo.json", bml_files={"BLUE": "blue_bml.json"})
print("Loaded:", sim.bml_files)
for _ in range(240):
    sim.tick(0.25)
print("Time:", sim.time, "Events:", len(sim.logs))
PY
```

실행 시 `python main.py scenarios/demo.json --blue-bml blue_bml.json`처럼 BML을 별도로 선택할 수도 있다. 명시적으로 넘긴 상대 경로는 **현재 작업 디렉터리** 기준이며, 과거 시나리오 내부의 `bml_files` 참조만 시나리오 파일 기준이다. `load_scenario(..., bml_files={})`는 내장 BML을 포함해 계획을 적용하지 않는 선택이다. `MISSION/validate_missions.py`는 저장된 8개 임무 사례 전용 검증기이며 임의의 새 BML을 검증하는 명령이 아니다.

최종 확인: 소속과 활성 부대 ID, 좌표 경계, 건물·교량 ID, 분기 필드, 다음 명령으로 넘어갈 완료 조건, 관측 가능한 정보만 사용했는지, `HOLD`/대기 중 자동 교전을 허용해도 되는지를 점검한다. 특정 전술의 완전한 지원이나 실물 무장 성능은 이 JSON의 로드 성공만으로 입증되지 않는다.

## Part 2 — Execution reference

_BML-lite: conditional, timed, phased missions, and mission directives_

The BML layer describes command intent. It does not replace sensing, FoW, local target selection,
terrain movement, weapon physics, or DoctrineEngine. Entity-target missions continue to pursue
perceived Tracks rather than live enemy ground truth.

### 1. Existing conditional mission trigger

`conditions` are reactive triggers evaluated while an order is active. All conditions must be true.
When they become true, `on_true` replaces the active order. Supported paths include:

- `sim.time`
- `self.loss_ratio`, `self.strength_ratio`
- `self.personnel`, `self.initial_personnel`
- `self.equipment`, `self.initial_equipment`
- `self.state`
- `self.time_in_order`
- `self.capability.<CAPABILITY>`
- `self.distance_to_objective`, `self.at_objective`
- `self.enemy_count_near`: count of current actionable Tracks inside the configured radius, using estimated positions. The legacy name is retained, but it no longer reads live enemy positions or survival.

Conditions are validated when the BML is loaded, before any live order changes: `lhs` must be one
of the paths above, numeric paths need a finite numeric `rhs`, `self.capability.*`/`self.at_objective`
need `true`/`false` with `==`/`!=`, and `self.state` needs a state-name string. Coordinates must be
finite and inside the scenario world.

Operators: `<`, `<=`, `>`, `>=`, `==`, `!=`.
`on_false` is evaluated when an order completes, before that order's objective and elapsed-time
metadata are cleared. Unknown branch tasks, condition paths, directives, and out-of-world
coordinates are rejected while loading the BML, before existing orders are replaced.

Example: defend until 50% total formation loss, then withdraw to Rally Point Alpha.

```json
{
  "unit": "B-INF_PLT-1",
  "task": "HOLD",
  "conditions": [
    {"lhs": "self.loss_ratio", "op": ">=", "rhs": 0.50}
  ],
  "on_true": {
    "id": "RALLY-ALPHA",
    "task": "WITHDRAW",
    "destination": [800, 1200]
  }
}
```

The condition does not artificially cause the loss. It only observes normal runtime state.

### 2. Timed mission start

Use `start_at_s` (alias `not_before_s`) for absolute scenario time.

```json
{
  "unit": "B-INF_PLT-1",
  "task": "MOVE_TO",
  "destination": [1600, 1100],
  "start_at_s": 300
}
```

Until T+300 the formation does not execute the movement order. Local doctrine may still react to
contacts unless mission directives prohibit the relevant reaction.

### 3. Deadline / latest desired arrival

Use `deadline_s` (alias `complete_by_s`). A deadline never increases movement speed and never
teleports a unit. If the mission is unfinished at the deadline, the simulator logs
`ORDER_DEADLINE_MISSED`. Without `on_deadline`, the formation continues trying.

```json
{
  "id": "RALLY-BRAVO",
  "unit": "B-INF_COY-1",
  "task": "MOVE_TO",
  "destination": [1800, 1500],
  "deadline_s": 600,
  "on_deadline": {
    "id": "MISSED-BRAVO",
    "task": "HOLD"
  }
}
```

This makes schedule feasibility an outcome of mobility, terrain, interference, and combat rather
than a scripted speed bonus.

### 4. Phase-organized BML

`phases` organize mission sequences while compiling into the same normal per-unit Order queue.
There is no second combat/phase engine.

```json
{
  "side": "BLUE",
  "replace_existing_orders": true,
  "phases": [
    {
      "id": "PHASE_I_ASSEMBLY",
      "start_at_s": 0,
      "deadline_s": 300,
      "missions": [
        {"unit": "B-INF_PLT-1", "task": "MOVE_TO", "destination": [900, 1000]},
        {"unit": "B-INF_PLT-2", "task": "MOVE_TO", "destination": [950, 1050]}
      ]
    },
    {
      "id": "PHASE_II_ATTACK",
      "start_at_s": 300,
      "missions": [
        {"unit": "B-INF_PLT-1", "task": "ATTACK_POSITION", "destination": [1800, 1500]},
        {"unit": "B-INF_PLT-2", "task": "ATTACK_POSITION", "destination": [1850, 1550]}
      ]
    }
  ]
}
```

A unit proceeds through its own queued phase missions. `start_at_s` is the synchronization gate
when several formations must not begin the next phase before a common scenario time. Phases do not
currently impose a global "all units must finish Phase I" barrier; this avoids artificial forced
synchrony. A future explicit phase-trigger manager can be added if a research scenario requires it.

Phase-level `start_at_s`, `deadline_s`, and `directives` are inherited by missions unless a mission
overrides them.

For a scenario with load-time `aggregations`, the parent formation is created before BML is
loaded. Address the active parent ID in BML; inactive source children cannot be commanded until
they are deaggregated. Scenario-embedded commands for a load-time aggregate belong in that
`aggregations[]` entry's `orders`; embedded orders on children that become inactive are rejected.

### 5. Doctrine profile versus mission directive

These are deliberately separate:

- **Doctrine profile**: standing local tactical reaction policy assigned to a unit in the scenario.
- **Mission directive**: temporary command-specific override carried by the active BML mission.

Scenario example:

```json
{
  "id": "R-INF_PLT-1",
  "side": "RED",
  "type": "INF_PLT",
  "doctrine_profile": "STAND_FAST",
  "pos": [2400, 1800]
}
```

Profiles are defined in `config/doctrine_profiles.json`. Profiles are not implicitly tied to BLUE,
RED, faction, or branch, so two same-side/same-type units may select different profiles.

Mission-specific stand-fast example:

```json
{
  "unit": "R-INF_PLT-1",
  "task": "DEFEND_AREA",
  "center": [2400, 1800],
  "radius_m": 100,
  "directives": {
    "hold_at_all_costs": true,
    "allow_withdrawal": false,
    "allow_break_contact": false
  }
}
```

Supported local-reaction directives currently include:

- `hold_at_all_costs`
- `allow_withdrawal`
- `allow_break_contact`
- `allow_artillery_displacement`
- `allow_indirect_fire_dispersion`
- `assault` — `false` keeps an ATTACK / ATTACK_UNIT / DESTROY_UNIT order at its stand-off line
  (support by fire only); by default the formation assaults once it has fire superiority

These directives override normal local reaction policy, not physical reality. A unit with no usable
weapon can be ordered not to withdraw, but it does not magically regain firepower.

### 6. Design priority

Runtime precedence is intentionally:

1. physical/capability constraints,
2. explicit active mission directives,
3. selected doctrine profile,
4. historical generic engine defaults.

BML remains mission intent; DoctrineEngine remains local tactical reaction. New doctrine profiles or
mission directives should not require side-specific hard-coded branches in the simulation engine.

### Engagement-range mission directive (v48)

A mission can temporarily override the unit doctrine's positioning policy:

```json
{
  "unit": "B-INF_PLT-1",
  "task": "DESTROY_UNIT",
  "target": "R-INF_PLT-1",
  "directives": {
    "engagement_range_policy": "COMBINED_ARMS",
    "engagement_range_fraction": 0.90
  }
}
```

`STANDOFF` preserves the historical behavior: the formation stops when its longest relevant operational
weapon reaches the perceived target. `COMBINED_ARMS` permits long-range weapons to fire while the
formation continues closing until the shortest relevant operational weapon is inside its selected
fraction of maximum range. `BALANCED` uses the midpoint. This changes maneuver/positioning only; weapon
range, hit probability and target eligibility remain unchanged.


### Target identity vs. target location (v48.5)

Enemy unit identity does **not** reveal its location. `ATTACK_UNIT` / `DESTROY_UNIT` may prioritize a
known Track for the named target, but if no own/shared Track exists the formation does not read the
scenario Ground Truth position and does not move toward that enemy automatically. An optional explicit
planning/intelligence reference can be supplied by the BML itself:

```json
{
  "unit": "B-INF_PLT-1",
  "task": "DESTROY_UNIT",
  "target": "R-RIFLE_SQD-1",
  "target_position": [210, 400]
}
```

`target_position` is commander-provided information, not a live link to the target. Once a Track exists,
perceived Track / last-known Track position takes precedence.

For normal combat orders where enemy identity is unknown, prefer coordinate-based `ATTACK_POSITION`:

```json
{
  "unit": "B-INF_PLT-1",
  "task": "ATTACK_POSITION",
  "destination": [210, 400],
  "persistent": false
}
```

This order requires no enemy unit ID. The formation advances toward the commanded coordinate and may
engage any actionable hostile contact encountered through the normal Track/Belief pipeline. With
`persistent: false`, reaching/clearing the objective allows the next queued mission to begin; with the
default `true`, the formation remains at the objective searching/holding for further contacts.

### Mounted / Dismounted transport orders (v49)
Transport behavior is capability-driven. A carrier is any formation with equipment whose resolved
platform metadata has `passengers > 0`; vehicle names are not hard-coded.

#### Organic mechanized infantry
```json
{"unit":"B-MECH-1","task":"DISMOUNT","destination":[420,310]}
{"unit":"B-MECH-1","task":"MOUNT"}
```
`DISMOUNT` may optionally include a destination. The carrier moves there, waits the configured
dismount time, then transfers all personnel elements marked `metadata.dismountable=true` to an
organic child infantry formation. Vehicle crews remain with the carrier. `MOUNT` rendezvous with
that child and transfers the surviving elements back.

#### Boarding non-organic friendly infantry
```json
{"unit":"B-RIFLE-SQD-2","task":"BOARD","carrier":"B-BRADLEY-EMPTY-1"}
{"unit":"B-BRADLEY-EMPTY-1","task":"DISEMBARK","passengers":"ALL"}
```
The boarding unit retains its own identity and TO&E while embarked; it is removed from independent
movement/combat until disembarked. Boarding requires same side, sufficient free seats, an operable
carrier crew, physical rendezvous within `embark_radius_m`, and the loading delay. Seats are assigned
to individual vehicle slots inside an aggregate carrier formation and stored in runtime metadata.

`BOARD` uses the known friendly carrier position. It does not create any enemy Ground Truth knowledge.

### Timed execution applies to transport and combat orders

`start_at_s` is a common absolute scenario-time gate, not a MOVE-only option. For example:

```json
{
  "unit": "B-MECH-1",
  "task": "DISMOUNT",
  "start_at_s": 300
}
```

The unit may react under its local doctrine before T+300, but the scheduled DISMOUNT itself cannot
execute before simulation elapsed time reaches 300 seconds. The same rule applies to BOARD, MOUNT,
ATTACK_POSITION and other compiled orders.


### v49.2 terrain / elevation extension
- `LAKE` polygon: non-amphibious vehicles/equipment cannot enter. FOOT formations may swim at a severe speed penalty; on first water entry, crew-served/heavy machine guns and anti-armor weapons are dropped and logged (`SWIM_HEAVY_WEAPONS_DROPPED`).
- `BUILDING` polygon: FOOT formations may occupy only via explicit `ENTER_BUILDING` / `EXIT_BUILDING` boundary-crossing orders during runtime; ordinary movement routes around operational footprints. Engine-level occupancy is intentionally unlimited regardless of echelon/personnel count, leaving force-to-building constraints to higher-level BML/COA generation. Operational buildings hard-occlude visual/direct-fire rays unless elevation clears the roof. Occupants are harder to detect/hit from outside. Structure-capable direct weapons and indirect-fire impacts reduce structural integrity; collapse destroys occupants.
- `ELEVATION` polygon: authored contour/plateau elevation in metres above the 0 m map datum. Overlapping contours use the highest value. Grade affects path passability and movement speed. Observation/direct-fire ray height is compared with building roof height, so elevated observers may see/fire over lower obstacles.
- Editor tools: `L` Lake, `K` Building, `V` Elevation; comma/period changes current contour elevation by 10 m; semicolon/apostrophe changes current building height by 1 m.
- Elevation is currently vector/step-contour rather than a continuous DEM. It is deliberately isolated behind `TerrainModel.elevation_at()` for later bilinear/raster replacement.

#### Attack a known building
```json
{"unit":"B-INF-PLT-1","task":"ATTACK_STRUCTURE","target_structure":"BLD1"}
```
Only direct weapons carrying the `STRUCTURE` target tag / `structure_capable` metadata participate. The order conveys knowledge of a static mapped structure, not an enemy-unit location. Artillery may include building IDs in the existing `STRIKE_INFRASTRUCTURE` target list.

### SECURE_AREA / bounded area defence (v49.4)
`SECURE_AREA` is a reactive bounded-defence mission. It occupies a center/radius (or polygon), engages perceived contacts, may manoeuvre/pursue only while the Track estimate remains inside the authorised pursuit boundary, and re-centers after contact. It never uses enemy Ground Truth.

```json
{
  "unit": "B-TANK-1",
  "task": "SECURE_AREA",
  "center": [300, 250],
  "radius_m": 180,
  "engagement_radius_m": 220,
  "pursuit_radius_m": 180,
  "return_to_center": true
}
```

`DEFEND_AREA` remains available. Set `pursue_within_area: true` to give it the same bounded pursuit behavior. Direct fire may reach a valid target outside the movement boundary if the unit already has a valid Track/LOS; the unit itself will not leave the pursuit boundary merely to chase it.

### BUILD_BARRICADE (v49.7)

A foot infantry/recon/special-operations formation may construct a preallocated 10 m HESCO MIL1-class barrier when its scenario metadata has `barricade_limit > 0`. The default is zero; this capacity represents barrier material/material-handling support allocated to the formation, not a permanently detached engineer crew.

```json
{
  "unit": "B-INF_PLT-1",
  "task": "BUILD_BARRICADE",
  "position": [420, 310],
  "heading_deg": 30
}
```

`heading_deg` is the outward/facing normal measured counter-clockwise from east. The protected side is the opposite side. Length is fixed at 10 m. Default construction time is 1200 s and may be overridden with `construction_time_s` for scenario calibration. The unit moves to the site, constructs the barrier, then consumes one of its allocated barrier sections.

### Explicit building entry / exit

Operational BUILDING footprints are hard movement obstacles for ordinary `MOVE_TO`, `ATTACK_POSITION`, pursuit, and retreat routing. A dismounted/FOOT formation crosses a building boundary only through an explicit building mission:

```json
{"unit":"B-INF_PLT-1","task":"ENTER_BUILDING","target_structure":"BLD1"}
```

`destination` may optionally identify a point inside the building; otherwise the building center is used.

```json
{"unit":"B-INF_PLT-1","task":"EXIT_BUILDING","target_structure":"BLD1","destination":[420,310]}
```

`EXIT_BUILDING.destination` must be outside that building. The simulation engine deliberately does **not** enforce building occupancy by echelon, personnel count, or authored capacity. Such constraints belong in the higher-level BML/COA generation policy if a scenario requires them.

### Assigned watch / guard bearing (v49.9)
For a halted/defending formation, an order may include `watch_heading_deg` (legacy alias `facing_deg`) to assign its principal observation/engagement bearing. The value is a desired bearing, not an instantaneous teleport: the formation slews toward it according to its sensor/orientation profile. Moving formations normally orient toward the movement axis; actionable targets, shared situational cues, or incoming-fire cues may temporarily take priority according to simulation doctrine.
