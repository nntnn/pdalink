# Wi-Fi Direct Design With Grill-Me

이 문서는 Wi-Fi Direct 경로를 진행하기 전에 `$grill-me` 방식으로 만든
질문 세트와 best answer 세트다. 코드로 확인 가능한 내용은 먼저 확인했고,
남은 설계 질문은 권장 답변까지 채워 넣었다.

## Current Code Facts

- Android `ProxyService`에는 `START_WIFI_DIRECT` action과 `WIFI_DIRECT` mode가
  이미 있다.
- 일반 `START_WIFI_DIRECT`는 이전 native peer 기록과 무관하게 저장된
  SSID/암호로 비영구 autonomous group을 만든다. native peer 재협상은
  `INVITE_WIFI_DIRECT_PEER`를 명시적으로 요청한 세션에서만 활성화한다.
- Wi-Fi Direct mode는 Android가 group owner를 만들고, Linux target을
  `http://192.168.49.1:<port>`로 표시한다.
- Android Wi-Fi Direct proxy port는 `8001`이고 P2P group-owner 주소
  `192.168.49.1`에 bind한다. ADB USB는 `127.0.0.1:8000`을 유지한다.
- USB/Product USB와 Wi-Fi Direct listener/lifecycle은 분리되어 있어 두 데이터
  경로를 동시에 사용할 수 있다. `STOP_WIFI_DIRECT`는 USB listener를 보존한다.
- Android runtime permission 요청도 mode별로 갈라져 있다. USB는 notification
  권한만, Wi-Fi Direct는 Android 버전에 따라 `NEARBY_WIFI_DEVICES` 또는
  `ACCESS_FINE_LOCATION`을 요청한다.
- Manifest의 `android.hardware.wifi.direct`는 `required=false`로 되어 있어
  USB-only 사용자를 막지 않는다.
- Linux에는 `scan`, `connect`, `connect-auto`, `probe`, `env`, `curl-test`,
  `doctor`가 있고, `connect-auto`는 `DIRECT-*` SSID를 고른 뒤 proxy probe까지
  수행한다.
- `tools/e2e_smoke.sh`는 ADB로 Android Wi-Fi Direct service를 시작하고,
  Linux가 이미 Direct link에 붙은 뒤 proxy/curl smoke를 수행하는 흐름이다.
- 기존 delivery 문서는 USB를 1차 제품 경로로 보고, Wi-Fi Direct productization은
  명시적으로 비목표로 둔 상태다.

## Decision Tree

### Q1. Wi-Fi Direct를 지금 제품 기본 경로로 올릴 것인가?

Best answer: 아니다. 지금은 `manual/diagnostic Wi-Fi Direct path`로 안정화한다.
USB/Product USB는 계속 제품 기본 경로로 두고, Wi-Fi Direct는 USB가 불편하거나
불가능한 상황의 보조 경로와 네트워크/proxy 진단 경로로 다듬는다.

Reason: Wi-Fi Direct는 Linux의 기존 Wi-Fi 연결을 빼앗을 수 있고, SSID/passphrase
전달, group lifecycle, proxy exposure, route exception 같은 제품 문제가 남아
있다. 반면 현재 코드에는 이미 수동 proxy-aware smoke가 있으므로 이 경로를 먼저
반복 가능하게 만드는 것이 가장 작고 검증 가능하다.

### Q2. 이번 Wi-Fi Direct slice의 성공 기준은 무엇인가?

Best answer: 한 명령 흐름으로 Android group, Linux join, proxy probe, HTTPS
curl smoke까지 재현 가능해야 한다. 1차 완료 기준은 proxy-aware traffic이다.
whole-system routing은 다음 slice로 둔다.

Acceptance:

- Android app에서 Wi-Fi Direct group이 생성되고 SSID/passphrase/target이
  표시된다.
- Linux가 `connect-auto --password ...` 또는 wrapper command로 Direct link에
  붙는다.
- `probe`가 `192.168.49.1:8000` 또는 `:8001` PAC endpoint를 확인한다.
- `curl-test https://ifconfig.me`가 Android egress를 통해 성공한다.
- 실패 시 `doctor --adb`가 route, missing tools, proxy port, Android logs를
  한 번에 보여준다.

### Q3. Linux UX는 lower-level Python 명령을 그대로 둘 것인가?

Best answer: lower-level Python 명령은 유지하되, 사용자-facing entrypoint는
`./tools/pdalink.sh wifi ...`로 추가한다.

Recommended commands:

```bash
./tools/pdalink.sh wifi scan
./tools/pdalink.sh wifi connect --password '...'
./tools/pdalink.sh wifi status
./tools/pdalink.sh wifi test
./tools/pdalink.sh wifi smoke --start-android
./tools/pdalink.sh wifi stop-android
```

Reason: 이미 `pdalink.sh usb/system/product-usb/watch`가 사용자 entrypoint다.
Wi-Fi Direct만 Python 직접 호출로 남기면 제품 표면이 갈라진다.

### Q4. Linux가 Android Wi-Fi Direct group을 자동으로 시작해야 하나?

Best answer: 제품 UX에서는 Android 앱에서 사용자가 직접 시작한다. 개발/진단
UX에서는 ADB가 있을 때만 `wifi smoke --start-android`를 허용한다.

Reason: Wi-Fi Direct product flow가 ADB에 의존하면 USB-free path라는 의미가
사라진다. 하지만 현재 개발 속도와 live smoke 재현성을 위해 ADB start는 debug
shortcut으로 계속 유용하다.

### Q5. SSID/passphrase 전달은 어떻게 할 것인가?

Best answer: 1차는 Android 화면의 SSID/passphrase를 사용자가 보고 Linux CLI에
넣는다. 자동 전달은 ADB debug status로만 허용하고, product path에서는 QR 또는
copy/share UX를 후속으로 둔다.

Reason: Linux가 Direct link에 붙기 전에는 Android가 띄운 local endpoint에
접근할 수 없다. 네트워크 밖에서 안전하게 passphrase를 가져오려면 ADB, QR,
Bluetooth, BLE, USB, cloud 같은 별도 channel이 필요한데 1차 범위가 아니다.

### Q6. Wi-Fi Direct proxy는 어떤 주소에 bind해야 하나?

Best answer: 최종 목표는 Wi-Fi Direct group owner address 또는 P2P interface에만
bind하는 것이다. 당장 코드 변경이 어렵다면 최소한 client source allowlist를
`192.168.49.0/24`로 제한한다.

Reason: 현재 `HttpProxyServer`는 Wi-Fi Direct mode에서 bind address가 `null`이라
Android의 모든 interface에 listen할 수 있다. 이 상태는 phone이 다른 Wi-Fi나
local network에 붙어 있을 때 proxy exposure risk가 있다. Wi-Fi Direct mode는
Linux peer만 접근 가능해야 한다.

### Q7. Wi-Fi Direct proxy에 인증 토큰을 넣을 것인가?

Best answer: 1차에서는 넣지 않는다. 대신 bind/interface/source restriction을
먼저 한다. non-local exposure가 남는 구조라면 token을 추가한다.

Reason: Wi-Fi Direct group은 WPA2 passphrase로 link-level access control을 이미
갖는다. 인증 토큰은 distribution UX와 client config 복잡도를 늘린다. 다만 proxy가
다른 interface에 노출될 수 있다면 토큰보다 먼저 bind/ACL을 고쳐야 한다.

### Q8. Android egress는 cellular-only여야 하나?

Best answer: 기본은 기존 정책처럼 cellular preferred, default fallback 허용이다.
다만 Wi-Fi Direct UI와 status에는 `cellular`인지 `default fallback`인지 명확히
보여준다. 후속 제품 옵션으로 `--cellular-only` 또는 app toggle을 둔다.

Reason: 개발과 진단에서는 fallback이 성공률을 높인다. 제품 tethering에서는
사용자가 "phone mobile data를 쓰는지"를 알아야 하므로 status 명확성이 필수다.

### Q9. Linux가 Direct network에 붙으면 기존 Wi-Fi 인터넷이 끊기는 문제는?

Best answer: 1차에서는 명시적 동작으로 받아들이고, command/status에 경고를
표시한다. 자동 연결이나 background watcher 대상으로 삼지 않는다.

Reason: 많은 Linux 노트북은 Wi-Fi radio 하나로 기존 AP와 Direct group을 동시에
안정적으로 유지하지 못한다. 사용자가 직접 `wifi connect`를 실행할 때만 기존 Wi-Fi
전환이 일어나야 한다.

### Q10. Wi-Fi Direct를 whole-system routing에도 바로 연결할 것인가?

Best answer: 바로 연결하지 않는다. 먼저 proxy-aware path를 안정화한다. 다음 slice에서
`system-start --proxy-url http://192.168.49.1:<port>`를 지원하되, 반드시 proxy host
route exception을 넣는다.

Reason: USB system routing은 proxy endpoint가 `127.0.0.1`이라 route loop가 없다.
Wi-Fi Direct는 proxy endpoint가 `192.168.49.1`이라 split-default routes를 추가하면
adapter가 Android proxy로 가는 connection 자체를 TUN으로 잡아먹을 수 있다.

Required next-slice rule:

```text
Before adding split-default routes, preserve a host route to 192.168.49.1
through the Wi-Fi Direct interface and record it in runtime state.
```

### Q11. DNS 정책은 Wi-Fi Direct에서 달라져야 하나?

Best answer: proxy-aware 1차에서는 DNS를 건드리지 않는다. system-routing slice에
들어갈 때는 USB와 같은 `active_override` DNS 정책을 재사용한다.

Reason: `curl -x`나 proxy env path에서는 DNS resolution 위치가 application과 proxy
동작에 따라 달라질 수 있지만, OS route를 바꾸지 않는 한 system DNS를 만질 이유가
없다.

### Q12. IPv6, ICMP, arbitrary UDP를 Wi-Fi Direct에서 지원할 것인가?

Best answer: 이번 Wi-Fi Direct slice에서는 지원하지 않는다. Wi-Fi Direct는 transport
entrypoint만 다르고 data path는 기존 Android HTTP proxy semantics를 따른다.

Reason: 프로젝트의 현재 공통 제한은 IPv4 TCP와 DNS 중심이다. Wi-Fi Direct만 별도
packet semantics를 약속하면 USB 경로와 제품 메시지가 갈라진다.

### Q13. Android group lifecycle은 어떻게 잡을 것인가?

Best answer: start는 idempotent, stop은 best-effort cleanup으로 둔다. `BUSY`일 때는
현재 코드처럼 existing group을 publish하고, stale group 제거 후 1회 retry한다.
다만 status에는 `created`, `existing`, `retrying`, `not active`를 구분해 표시한다.

Reason: Android Wi-Fi P2P stack은 `BUSY`와 stale group이 흔하다. 사용자가 실패
원인을 볼 수 있어야 하고, stop이 다른 Android Wi-Fi 상태를 과도하게 건드리면 안 된다.

### Q14. Android UI는 어떻게 바꿀 것인가?

Best answer: 기존 단일 화면을 유지하되 Wi-Fi Direct section을 더 명확히 만든다.
SSID, passphrase, Linux commands, proxy target, egress, client count, last error를
같은 block에 보여준다. Notification에는 passphrase를 넣지 않는다.

Reason: 사용자는 phone 화면을 보고 Linux 명령을 입력해야 한다. 반면 notification은
잠금화면에 노출될 수 있으므로 secret을 넣지 않아야 한다.

### Q15. Android logs에 passphrase를 남길 것인가?

Best answer: debug build에서는 허용할 수 있지만, product-oriented mode에서는
logcat에 passphrase를 남기지 않는다. UI에는 보여주되 status log에는 mask한다.

Reason: 현재 `status("Group password: ...")`는 logcat에도 남는다. ADB가 켜진 개발
환경에서는 편하지만, Wi-Fi Direct를 제품 경로에 가깝게 다듬을수록 credential logging은
제거해야 한다.

### Q16. Linux `connect-auto`는 어떤 network를 골라야 하나?

Best answer: 기본은 현재처럼 `DIRECT-` pattern match 후 active network를 우선하고,
그다음 signal이 강한 network를 고른다. wrapper에는 `--ssid`, `--ssid-pattern`,
`--iface` override를 노출한다.

Reason: Android Wi-Fi Direct SSID는 고정하기 어렵고 주변에 여러 Direct network가
있을 수 있다. 자동 선택은 편의 기능이어야 하고, ambiguous case에서는 사용자가 직접
SSID를 지정할 수 있어야 한다.

### Q17. Proxy port fallback은 어디까지 할 것인가?

Best answer: Wi-Fi Direct는 현재 `8000,8001`을 유지한다. Linux wrapper/status는
항상 probe로 실제 port를 찾아 사용한다.

Reason: Wi-Fi Direct path는 Android-local service port 충돌만 피하면 된다. USB처럼
local forward port와 remote port가 독립적으로 있는 구조가 아니므로 과한 fallback은
진단을 어렵게 만든다.

### Q18. Wi-Fi Direct status는 어디에 저장할 것인가?

Best answer: 1차에서는 persistent state를 만들지 않는다. `doctor/status`가 live
system state를 읽어 출력한다. system-routing slice부터 runtime state에 selected SSID,
interface, proxy URL, host route exception, route/DNS changes를 기록한다.

Reason: proxy-aware Wi-Fi Direct path는 Linux route mutation을 하지 않으므로 cleanup
state가 필요 없다. state file은 route mutation이 들어갈 때부터 중요해진다.

### Q19. 자동 연결/autostart에 Wi-Fi Direct를 넣을 것인가?

Best answer: 넣지 않는다. Wi-Fi Direct는 explicit command로만 연결한다.

Reason: Wi-Fi Direct connect는 사용자의 현재 Wi-Fi network를 바꿀 수 있다. background
watcher가 이를 자동으로 수행하면 기존 인터넷/SSH/session을 끊을 수 있다.

### Q20. Existing Windows connectivity coexistence 관점에서 위험한 점은?

Best answer: USB/ADB forward와 Windows route를 건드리지 않는 한 기존 desktop
connectivity와 직접 충돌하지 않는다. 다만 Wi-Fi Direct join은 host의 Wi-Fi
connectivity를 바꿀 수 있으므로 Windows/WSL host에서는 기존 recovery path를 유지한
상태에서 manual proxy smoke만 한다.

Reason: 기존 delivery principle은 unrelated desktop connectivity와 existing routes를
건드리지 않는 것이다. Wi-Fi Direct 작업도 이 원칙을 유지해야 한다.

### Q21. 어떤 테스트를 추가해야 하나?

Best answer:

- Python unit tests: wrapper command construction, Wi-Fi Direct status next steps,
  route warning, ambiguous SSID behavior.
- Shell syntax: `bash -n tools/*.sh`.
- Android build: `./tools/build_android_manual.sh`.
- Live manual smoke: Android start, Linux `wifi connect`, `wifi status`,
  `wifi test`, cleanup.
- Security regression: Wi-Fi Direct mode proxy가 non-P2P source를 거부하거나
  P2P address에만 bind하는지 확인.

Reason: 이 slice의 위험은 algorithmic packet behavior보다 orchestration, exposure,
diagnostics에 있다.

### Q22. 이번 작업의 구현 순서는?

Best answer:

1. Add `./tools/pdalink.sh wifi ...` wrappers over existing Python commands and
   `tools/e2e_smoke.sh`.
2. Improve Android Wi-Fi Direct status text: mode-specific command hints,
   egress clarity, group state clarity.
3. Stop logging raw group passphrase to logcat; show it only in UI/debug snapshot
   where needed.
4. Restrict Wi-Fi Direct proxy exposure by binding to the P2P address when
   available or rejecting non-`192.168.49.0/24` clients.
5. Add `wifi status`/`doctor` output that explains missing group, wrong route,
   wrong SSID, missing `nmcli`, unreachable proxy, and Android permission errors.
6. Update `docs/MANUAL_TEST.md`, `android/README.md`, and `linux/README.md` so
   Wi-Fi Direct has a first-class manual flow.
7. Run unit tests, shell syntax checks, Android manual build, and one live
   Wi-Fi Direct smoke when hardware is available.

## Proposed First Slice

Do not start by adding full-tunnel Wi-Fi Direct. Start with a product-shaped
manual Wi-Fi Direct path:

```text
Android app Start Wi-Fi Direct
  -> shows SSID/passphrase/proxy target/egress
Linux ./tools/pdalink.sh wifi connect --password ...
  -> nmcli joins DIRECT-* network
  -> probe confirms PAC
Linux ./tools/pdalink.sh wifi test
  -> curl -x http://192.168.49.1:<port> https://ifconfig.me
Android UI/log
  -> shows active client, bytes, last request, last error
```

This keeps the slice small, validates the existing transport, and leaves the
system-routing version as a clean follow-up with an explicit host-route exception
design.
