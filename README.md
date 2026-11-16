# Qwen 3.8 Flash Next 한국어 출력 제약

Strata에서 Qwen 3.8 Flash Next로 한국어 답변을 생성할 때 한자 혼입을 줄이는 소스 패치입니다. 다음 토큰을 고르기 전에 UTF-8 바이트를 검사합니다. ‘아홉’처럼 여러 토큰으로 나뉜 한글은 끝까지 조합할 수 있습니다.

[설치·복구 웹 안내](docs/index.html) · [작동 흐름도](docs/workflow.md) · [설치 에이전트 안내](INSTALL_FOR_AGENTS.md) · [보안·개인정보 감사](docs/security-audit.md)

웹 안내는 GitHub에서 내려받아 `docs/index.html`을 브라우저로 열면 됩니다. 아래 절차만으로도 설치·검증·복구할 수 있습니다.

## 지원 범위

- Linux의 NVIDIA CUDA, Strata 네이티브 단일 요청 경로, Qwen3.8 Flash Next의 검증된 토크나이저가 대상이다. 기존 운영 검증은 ISTA-DASLab IQ3_S에서 했다. IQ4_XS는 같은 토크나이저를 쓰는 특정 Unsloth 팩에서 네이티브 제약 경로를 별도로 검증했다. 사용하는 팩과 실행 구성이 다르면 아래 설치 검사를 실행한다.
- Strata 엔진 **0.1.60을 기반으로 개발하고 실모델에서 검증했습니다.** 설치기는 지정한 Strata 소스에 패치를 시도합니다. 이후 버전의 동작은 보장하지 않으므로 빌드와 응답 검증을 거쳐 적용하세요. [검증 기준 커밋](https://github.com/Niko1221/Strata/commit/09b603886925994d0d4461857b01321ef3aa431c)
- 자동 정책에서 한자나 원문 보존 지시가 요청에 있으면 요청 전체를 기존 경로로 보낸다. JSON·도구 호출·이미지·명시적 추론 요청도 기존 경로를 사용한다. 적용된 요청에서는 자동 추론과 대화 체크포인트가 꺼진다.
- 한자 강제 차단과 한자 전용 출력 지시가 충돌하면 본문이 빈 채로 `stop`이 올 수 있다. 한자 출력을 원하면 요청 JSON에 `"ko_output_policy": "off"`를 지정한다. 한자 구간별 자동 치환과 빈 결과 자동 복구는 실험 기능이다.

## 작동 구조

컨트롤러가 요청별로 `auto`, `on`, `off`를 결정합니다. `on`이면 CUDA 엔진은 현재 UTF-8 상태에서 허용되는 토큰만 뽑고, 확정된 토큰으로 다음 상태를 갱신합니다. `off`나 `off/preserve`면 원래의 생성 경로를 사용합니다. 적용 여부는 응답 헤더 `X-Strata-Ko-Policy`에서 확인합니다.

## 설치

Python 3.12 이상, Git, CMake, Ninja, CUDA 툴킷과 정상 동작하는 Qwen 모델·Strata 팩·서버 JSON이 필요합니다. ZIP을 푼 디렉터리에서 Bash를 엽니다. `STRATA`에는 사용할 Git 체크아웃의 경로를 입력합니다. `PACK`, `PROFILE`, `WORKDIR`에는 각각 기존 팩, 서버 JSON, 원래 서버의 작업 디렉터리를 입력합니다. 프로필의 `args`에는 `"--pack", "…"` 두 항목이 있어야 합니다.

배포 ZIP과 체크섬 파일을 같은 폴더에 두고 확인합니다.

```bash
sha256sum -c qwen38-fn-ko-output-v0.1.2.zip.sha256 &&
unzip qwen38-fn-ko-output-v0.1.2.zip &&
cd qwen38-fn-ko-output-v0.1.2
```

```bash
ADDON="$PWD"
STRATA=/path/to/Strata
RUNTIME="$HOME/.local/share/qwen38-fn-ko-output/v0.1.2"
PACK=/path/to/existing/pack
PROFILE=/path/to/existing/strata-server.json
WORKDIR=/path/to/original/working-directory
PORT=18095

PYTHON="$HOME/.local/share/qwen38-fn-ko-output/venv-0.1.2/bin/python"
python3 -m venv "$HOME/.local/share/qwen38-fn-ko-output/venv-0.1.2" &&
"$PYTHON" -m pip install -r "$STRATA/requirements.txt" &&
"$PYTHON" "$ADDON/release.py" prepare --strata "$STRATA" --pack "$PACK" --runtime "$RUNTIME"
```

`prepare`는 지정한 체크아웃의 현재 커밋을 별도 경로에 복사하고 그 커밋을 `runtime.json`에 기록합니다. 원본 체크아웃은 그대로 두며 커밋되지 않은 변경은 내보내지 않습니다. 변경할 소스 구간을 유일하게 찾을 수 없거나 토크나이저가 일치하지 않으면 어느 단계에서 실패했는지 표시합니다. 준비가 끝난 새 런타임은 `off`로 시작합니다.

### 검증 기준 소스가 필요한 경우

사용할 체크아웃이 없거나 0.1.60 기준으로 재현하려면 아래 명령으로 별도 소스를 받으세요. 이후 위 명령의 `STRATA`에 이 경로를 사용합니다.

```bash
STRATA="$HOME/src/strata-ko-base"
UPSTREAM=09b603886925994d0d4461857b01321ef3aa431c
mkdir -p "$(dirname "$STRATA")" &&
mkdir "$STRATA" &&
git -C "$STRATA" init &&
git -C "$STRATA" fetch --depth 1 https://github.com/Niko1221/Strata.git "$UPSTREAM" &&
git -C "$STRATA" checkout --detach FETCH_HEAD
```

### 빌드와 시작

RTX 3090 기준 CUDA 12.6 빌드 예시다. 카드에 맞춰 `86-real`을 조정하고 CUDA 툴킷이 여러 개라면 CMake에 설치 위치를 명시한다. `strata`가 로드하는 빌드 산출물은 `build` 폴더에 함께 둔다.

```bash
GPU_ARCH=86-real
cmake -S "$RUNTIME/source" -B "$RUNTIME/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES="$GPU_ARCH" \
  -DSTRATA_ENABLE_CUDA=ON -DSTRATA_BUILD_SPLIT=OFF \
  -DSTRATA_BUILD_DSV41=OFF -DSTRATA_BUILD_TESTS=OFF \
  -DSTRATA_NATIVE_EXPERTS=ON -DSTRATA_OWN_CPU_QUANT=ON \
  -DSTRATA_Q6K_EXPERTS=ON -DSTRATA_MMQ_KQUANTS=ON
cmake --build "$RUNTIME/build" --target strata -j 2

"$PYTHON" "$ADDON/release.py" configure --runtime "$RUNTIME" \
  --profile "$PROFILE" --working-dir "$WORKDIR" --port "$PORT"
"$PYTHON" "$ADDON/release.py" serve --runtime "$RUNTIME" --offline
```

별도 서버는 `127.0.0.1`에 바인드합니다. 두 모델이 GPU 메모리에 함께 들어가지 않으면 기존 서버를 중지한 뒤 실행합니다. 런처는 기존 JSON을 읽고 새 엔진과 토크나이저 경로를 넣은 임시 설정을 사용합니다. 웹 UI에서 임시 설정을 바꾸면 재시작할 때 그 값이 사라집니다.

예시의 `--offline`은 원격 이미지 가져오기, MCP 서버 실행, 로드 전 명령 등과 충돌하는 기존 프로필을 시작 단계에서 알립니다. 기존 프로필의 네트워크나 도구 연동이 필요하면 그 설정을 검토한 뒤 실행 옵션을 선택합니다. 이 옵션은 Strata의 애플리케이션 정책이며, Git이나 pip의 설치 단계에는 적용되지 않습니다.

## 적용 확인과 롤백

서버가 로드된 뒤 다른 터미널에서 위 변수들을 설정하고 아래 순서대로 확인한다. 서버가 인증을 요구하면 테스트 셸의 `STRATA_API_KEY` 환경변수로 기존 키를 전달한다.

```bash
# 초기 OFF 및 강제 ON 거부 확인
"$PYTHON" "$ADDON/smoke.py" --port "$PORT" --disabled &&
"$PYTHON" "$ADDON/release.py" enable --runtime "$RUNTIME" &&
"$PYTHON" "$ADDON/smoke.py" --port "$PORT" &&
"$PYTHON" "$ADDON/release.py" status --runtime "$RUNTIME"

# 재시작 없이 끄기와 확인
"$PYTHON" "$ADDON/release.py" disable --runtime "$RUNTIME"
"$PYTHON" "$ADDON/smoke.py" --port "$PORT" --disabled
```

`enable`과 `disable`은 그 뒤 들어오는 요청에 적용됩니다. 적용 상태 검증이 실패하면 `disable`을 실행하고 검증 메시지를 확인합니다. 정책을 끈 채로 패치 서버를 계속 쓸 수 있습니다.

**기존 엔진으로 복구하려면** 패치 서버를 띄운 터미널에서 `Ctrl+C`로 종료합니다. 기존 Strata를 원래 명령과 프로필로 시작하고, 기존 클라이언트 주소로 돌아간 뒤 `/health`에서 모델이 로드됐는지 확인합니다. 설치 명령은 기존 서비스 설정과 모델 파일을 보존합니다.

응답의 `X-Strata-Ko-Policy`는 `on`, `off`, 또는 `off/preserve`처럼 해제 이유가 붙은 값을 보여 준다. Chat Completions의 요청 JSON에서는 `auto`(기본), `off`, `on`을 고를 수 있다. 지원 범위를 벗어난 요청 또는 운영 스위치가 꺼진 상태에서 `on`을 강제하면 HTTP 400을 반환한다. 기본 검증은 ‘아홉 시’, `東京` 보존, 한자 전용 요청의 `off`/`on` 차이를 확인한다.

## 구성과 검증 이력

- 에이전트에 설치를 맡기려면 [설치 에이전트 안내](INSTALL_FOR_AGENTS.md)를 전달합니다. 적용할 팩과 프로필은 로컬 경로로 지정하고, API 키나 프로필 전체 내용은 채팅이나 공개 이슈에 붙이지 않습니다.
- `release.py`는 소스 준비, 런처 설정, 시작과 운영 스위치를 제공한다. `smoke.py`는 서버에 검증 요청을 보내며, `python -m unittest discover -v`는 GPU 없이 실행하는 회귀 검사다.
- 기존 두 IQ3_S 운영 서버에서 검증한 정책을 패키지화했습니다. `v0.1.2`에서는 기준 커밋과 Strata `v0.2.15` 소스에 패치하고 같은 248,320개 토큰 전이표를 생성했습니다. `v0.2.15`의 Control 0.1.61에서 모의 HTTP 해제 검사도 통과했습니다. 이 Strata 릴리스의 엔진은 0.1.60이며, 해당 소스의 CUDA 빌드와 실모델 구동은 위 절차에서 확인하세요. [Strata v0.2.15 릴리스](https://github.com/Niko1221/Strata/releases/tag/v0.2.15)

## 라이선스

이 프로젝트에서 추가한 코드와 문서는 [Apache License 2.0](LICENSE)을 따릅니다. Strata에서 가져온 코드에는 [Strata의 MIT 라이선스 및 저작권 고지](licenses/Strata-MIT.txt)가 적용됩니다. [NOTICE](NOTICE)에 출처를 명시했습니다.
