# 치지직 방송 듣고 채팅 쳐주는 봇

스트리머가 방송에서 하는 말을 듣고, 그에 맞춰 채팅을 쳐주는 봇입니다.
혼자 방송하는데 채팅창이 조용할 때, 옆에서 맞장구쳐주는 시청자 한 명을
만들어 두는 게 목표입니다.

```
방송 오디오 ──ffmpeg──▶ 소리 ──VAD──▶ 말한 구간 ──Whisper──▶ 글자
                                                              │
채팅창 ──WebSocket──▶ 지금 분위기                              ▼
                                   "지금 말해도 되나?"  (Director)
                                                              │
                                                              ▼
                                "무슨 말을 할까?"  (Claude) ──▶ 안전 필터 ──▶ 채팅 전송
```

스트리머 PC 에 아무것도 설치하지 않습니다. 당신 컴퓨터에서 방송을
'보면서' 듣고 채팅을 칩니다.

---

## ⚠️ 먼저 읽어주세요

1. **치지직은 봇용 공식 채팅 API 를 공개하지 않았습니다.** 이 봇은 공개된
   커뮤니티 구현체에서 확인한 비공식 프로토콜을 씁니다. 네이버가 예고 없이
   바꾸면 어느 날 갑자기 멈출 수 있습니다. 그때는 `python run.py --check` 가
   어디가 깨졌는지 알려줍니다.

2. **스트리머 동의를 받고 쓰세요.** 그리고 봇 계정 닉네임에 `봇` 처럼
   봇임을 알 수 있는 표시를 두는 걸 권합니다. 사람인 척 꾸미는 자동 채팅은
   플랫폼 약관의 자동화·가짜 활동 규정에 걸릴 수 있고, 계정이 정지될 수
   있습니다. 이건 코드로 막아줄 수 없는 부분입니다.

3. **네이버 쿠키(`NID_AUT`, `NID_SES`)는 비밀번호와 거의 같습니다.**
   이 값이 있으면 그 계정으로 로그인할 수 있습니다. 남에게 보여주지 마세요.
   `.env` 는 `.gitignore` 에 들어 있습니다.

4. **반응이 10~30초 늦습니다.** 방송 송출 자체가 그만큼 지연되기 때문에,
   방송 화면을 받아서 듣는 방식으로는 피할 수 없습니다. 실시간에 가깝게
   하려면 스트리머 PC 에서 마이크를 직접 받아야 합니다.

5. **처음에는 `dry_run: true` 로 한참 돌려보세요.** 채팅을 보내지 않고
   "이런 말을 보냈을 것"만 로그에 찍습니다. 말투가 마음에 들 때까지 보고
   나서 실제 전송으로 바꾸는 걸 권합니다.

---

## 준비물

| 필요한 것 | 비고 |
|---|---|
| Python 3.10 이상 | `python3 --version` |
| ffmpeg | 방송 오디오 받는 데 씀 |
| Claude API 키 | https://platform.claude.com/ |
| 네이버 계정 | 채팅을 **보낼** 때만 필요 (봇용 계정 권장) |
| GPU | 없어도 됨. 있으면 음성인식이 훨씬 빠름 |

---

## 설치

```bash
git clone <이 저장소>
cd chatbottest1
```

```bash
python3 -m venv .venv && source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

음성인식 패키지는 용량이 커서(수백 MB) 따로 설치합니다.

```bash
pip install faster-whisper
```

ffmpeg 설치:

```bash
# macOS
brew install ffmpeg
```

```bash
# Ubuntu / Debian
sudo apt install ffmpeg
```

Windows 는 https://ffmpeg.org/download.html 에서 받아 PATH 에 추가하세요.

---

## 설정

### 1. 비밀값 — `.env`

```bash
cp .env.example .env
```

`.env` 를 열어 채웁니다.

```
ANTHROPIC_API_KEY=sk-ant-...
NID_AUT=...
NID_SES=...
```

**쿠키 얻는 방법** (PC 크롬 기준)

1. `chzzk.naver.com` 에 로그인
2. `F12` → **Application** 탭 → 왼쪽 **Storage ▸ Cookies ▸ https://chzzk.naver.com**
3. `NID_AUT`, `NID_SES` 의 **Value** 를 그대로 복사

쿠키는 시간이 지나면 만료됩니다. 봇이 "읽기 전용으로 접속합니다" 라고
하면 다시 복사해주세요.

### 2. 나머지 설정 — `config.yaml`

```bash
cp config.example.yaml config.yaml
```

최소한 채널 ID 만 바꾸면 됩니다. 방송 주소가
`https://chzzk.naver.com/live/abc123...` 이면 `abc123...` 부분입니다.

```yaml
channel:
  id: "abc123def456..."
```

---

## 점검

실행 전에 이걸 먼저 돌려보세요. 어디가 막혔는지 알려줍니다.

```bash
python run.py --check
```

```
치지직 채팅봇 자체 점검
========================================================
  ✓ ffmpeg — /usr/bin/ffmpeg
  ✓ faster-whisper 설치됨
  ✓ ANTHROPIC_API_KEY — 있음
  ✓ 네이버 쿠키 — NID_AUT / NID_SES 있음

치지직 API
--------------------------------------------------------
  ✓ 네이버 로그인 — userIdHash=a1b2c3d4…
  ✓ 방송 상태 — ON · 오늘도 롤 · 시청자 42명
  ✓ chatChannelId — N1W6yx
  ✓ 채팅 accessToken — 발급됨
  ✓ 오디오(HLS) 주소 — https://livecloud.../playlist.m3u8…

채팅 WebSocket
--------------------------------------------------------
  ✓ 채팅방 접속 (읽기+쓰기) — wss://kr-ss6.chat.naver.com/chat
```

방송이 꺼져 있으면 채팅방까지는 확인하지 못합니다. 방송 중에 다시
돌려보세요. Claude 실제 호출을 건너뛰려면 `--no-llm-check` 를 붙입니다.

---

## 실행

처음에는 보내지 않고 지켜봅니다.

```bash
python run.py --dry-run
```

```
12:34:01 INFO    chatbot.app   채널: abc123def456
12:34:01 INFO    chatbot.app   모드: DRY-RUN (채팅 안 보냄, 로그만)
12:34:02 INFO    chatbot.chzzk 방송 중: 오늘도 롤 (시청자 42명)
12:34:03 INFO    chatbot.chzzk 채팅방 접속 완료 (읽기+쓰기)
12:34:20 INFO    chatbot.app   들림: 아 이거 어떻게 해야 되지
12:34:22 INFO    chatbot.app   [DRY-RUN] 보냈을 채팅: 천천히 해도 돼요   (질문에 반응)
12:35:10 INFO    chatbot.app   들림: 채팅창 다들 있어요
12:35:12 INFO    chatbot.app   [DRY-RUN] 보냈을 채팅: 저 있어요 ㅋㅋ   (호출에 응답)
```

말투가 마음에 들면 실제로 보냅니다.

```bash
python run.py --send
```

`config.yaml` 의 `bot.dry_run` 으로 기본값을 정해두고, 위 두 옵션으로
그때그때 덮어쓸 수 있습니다. 끌 때는 `Ctrl+C`.

자세한 로그를 보려면:

```bash
python run.py --dry-run --debug
```

---

## 말투 바꾸기

`config.yaml` 의 `persona` 를 고치면 됩니다. 여기가 봇의 성격입니다.

```yaml
persona:
  name: "따숨"
  description: |
    스트리머를 오래 봐온 단골 시청자.
    조용하지만 다정하고, 리액션이 과하지 않다.
    반응은 짧고 담백하게. 억지 밈이나 유행어를 남발하지 않는다.
  interests:
    - "게임 실력 칭찬"
    - "스트리머 컨디션 챙기기"
  avoid:
    - "정치, 종교, 성적인 얘기"
    - "훈수, 지적, 가르치려는 말투"
```

`description` 을 구체적으로 쓸수록 말투가 잘 잡힙니다. 바꾼 뒤에는
`--dry-run` 으로 다시 확인해보세요.

---

## 도배 안 하게 만들기

봇이 미움받는 가장 빠른 길은 도배입니다. 그래서 "무슨 말을 할까"보다
"지금 말해도 되나"를 먼저, 보수적으로 판단합니다.

```yaml
director:
  min_interval_sec: 45          # 최소 이 간격은 쉼
  max_per_10min: 8              # 10분에 최대 8개
  lull_sec: 25                  # 이만큼 조용하면 말 걸어보기
  min_new_chars: 25             # 새로 들린 말이 이만큼 쌓여야 반응
  startup_grace_sec: 20         # 켜고 나서 이만큼은 듣기만
  attempt_interval_sec: 10      # Claude 에게 묻는 최소 간격
  human_chat_backoff:
    window_sec: 60
    messages: 8                 # 1분에 8개 넘게 올라오면
    interval_multiplier: 2.5    # 간격을 2.5배로 늘림 (사람들에게 양보)
```

마지막 `human_chat_backoff` 가 중요합니다. 사람들이 이미 활발하게 채팅
중이면 봇은 뒤로 물러납니다. 봇이 필요한 건 조용할 때니까요.

그리고 Claude 자신도 "지금은 말 안 하는 게 낫다"고 판단하면 그냥 넘어갑니다
(`speak=false`). 로그에 `안 보냄 (...)` 으로 찍힙니다.

보내기 직전에는 마지막 필터를 한 번 더 거칩니다.

```yaml
safety:
  max_chars: 90           # 너무 길면 자름
  dedupe_window: 12       # 최근 12개와 비슷하면 안 보냄
  similarity_limit: 0.82  # 중복 판정 기준(낮을수록 깐깐)
  blocked_words: []
```

`similarity_limit` 기본값은 꽤 깐깐합니다. 짧은 한국어 문장은 글자 몇 개만
겹쳐도 비슷하다고 나오기 때문에, 애매하면 그냥 안 보내는 쪽을 택했습니다.
"할 말을 너무 자주 삼킨다" 싶으면 `0.9` 정도로 올리세요.

---

## 비용

채팅 한 줄 만들 때 입력 약 1,100 토큰 / 출력 약 100 토큰 정도 씁니다.
기본 설정(10분에 최대 8개 = 시간당 최대 48개) 기준입니다.

| 모델 | 한 줄당 | 시간당 (최대로 떠들 때) | 4시간 방송 |
|---|---|---|---|
| `claude-opus-5-5` (기본) | 약 $0.0064 | 약 $0.31 | 약 $1.2 |
| `claude-haiku-5-5` | 약 $0.00016 | 약 $0.008 | 약 $0.03 |

채팅 한 줄 쓰는 일에는 Haiku 로도 보통 충분합니다. 바꾸려면 한 줄만
고치면 됩니다.

```yaml
llm:
  model: "claude-haiku-5-5"
```

실제로는 위 표보다 적게 듭니다. 봇이 "말 안 함"으로 판단하는 경우가
많아서 상한까지 쓰는 일은 드뭅니다.

---

## 음성인식 모델 고르기

```yaml
stt:
  model: "small"     # tiny / base / small / medium / large-v3
  device: "auto"     # auto / cpu / cuda
```

- GPU 없음 → `small` 까지가 현실적입니다. `medium` 이상은 CPU 에서 너무 느려
  밀립니다(밀리면 오래된 구간을 버리고 최신 것만 처리합니다).
- GPU 있음 → `medium` 또는 `large-v3` 를 쓰면 한국어 인식이 꽤 정확합니다.

모델 없이 파이프라인만 테스트하려면:

```yaml
stt:
  backend: "dummy"
```

---

## 잘 안 될 때

| 증상 | 확인할 것 |
|---|---|
| `설정 파일이 없습니다` | `cp config.example.yaml config.yaml` |
| `channel.id 를 실제 ... 채워주세요` | 방송 주소 `/live/` 뒤의 문자열 |
| `읽기 전용으로 접속합니다` | 쿠키 만료. `NID_AUT`/`NID_SES` 다시 복사 |
| `ffmpeg 를 찾을 수 없습니다` | ffmpeg 설치 후 PATH 확인 |
| 아무 말도 안 들림 | `--debug` 로 보고, `audio.vad.silence_threshold` 를 낮춰보세요 (예: `0.006`) |
| 엉뚱한 말이 들림 | 배경음악이 크면 음성인식이 어렵습니다. `stt.model` 을 올려보세요 |
| 채팅을 거의 안 침 | `director.min_new_chars` 를 낮추고 `min_interval_sec` 를 줄여보세요 |
| 채팅을 너무 자주 침 | 반대로 올리세요. `max_per_10min` 도 함께 |
| `걸러짐 (최근 메시지와 중복)` 이 잦음 | `safety.similarity_limit` 을 `0.9` 로 |
| 채팅방 접속이 안 됨 | 치지직이 프로토콜을 바꿨을 수 있습니다. `src/chatbot/chzzk/protocol.py` |

---

## 구조

```
run.py                        실행 파일 (CLI)
config.example.yaml           설정 예시
src/chatbot/
  config.py                   설정 로딩 + 검증
  app.py                      파이프라인 연결
  doctor.py                   --check 자체 점검
  chzzk/
    protocol.py               채팅 프로토콜 (순수 함수, 테스트 대상)
    api.py                    REST (방송 상태 / 토큰 / 오디오 주소)
    chat_client.py            채팅 WebSocket (접속·전송·재접속)
  audio/
    hls_source.py             ffmpeg 로 오디오 받기
    vad.py                    말한 구간만 잘라내기
  stt/
    faster_whisper_stt.py     한국어 음성인식 (+ 환각 걸러내기)
    dummy.py                  모델 없이 테스트용
  brain/
    transcript.py             들린 말 / 채팅 분위기 버퍼
    director.py               언제 말할지
    persona.py                프롬프트
    generator.py              Claude 호출
    safety.py                 보내기 직전 마지막 필터
```

테스트:

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q
```

92개 테스트가 프로토콜 프레임, 발화 구간 분리, 도배 방지 규칙, 안전 필터,
그리고 오디오→음성인식→버퍼 경로를 (실제 ffmpeg 로) 검증합니다.

---

## 앞으로 해볼 수 있는 것

- **지연 줄이기**: 스트리머 PC 에서 마이크를 직접 받기 (OBS 가상 오디오 →
  같은 VAD/STT 경로에 연결). `audio/hls_source.py` 자리에 로컬 오디오
  입력을 끼우면 됩니다.
- **후원·구독 반응**: `protocol.py` 가 이미 후원(`93102`)을 파싱합니다.
  `app._on_chat` 에서 분기해 반응을 따로 만들 수 있습니다.
- **스트리머가 봇을 부르면 답하기**: 채팅에서 봇 닉네임이 언급될 때
  트리거를 하나 더 추가.
