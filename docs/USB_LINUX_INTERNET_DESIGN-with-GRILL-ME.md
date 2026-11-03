# USB Linux Internet Design With Grill-Me

이 문서는 `$grill-me` 세션에서 합의한 1차 설계 산출물이다.

목표는 다음이다.

```text
Android internet <-> USB <-> Linux OS internet
```

최종 제품 경험은 사용자가 Linux에 PdaLink를 설치하고, 명시적으로 신뢰한
Android 기기를 USB로 연결했을 때 선택 설정에 따라 자동으로 Linux 인터넷
경로가 준비되는 것이다.

## 1. Scope

### 1차 목표

- Android USB debugging과 `adb forward`를 사용하는 구조를 1차 목표로 둔다.
- Android 앱은 USB mode에서 local-only HTTP/HTTPS `CONNECT` proxy를 제공한다.
- Linux는 Android proxy를 `127.0.0.1` proxy로 노출한다.
- Linux OS 전체 IPv4 TCP 트래픽과 UDP/53 DNS를 `pdalink0` TUN interface로 받는다.
- Linux TUN adapter는 TCP를 Android HTTP `CONNECT` proxy로 변환하고, UDP/53 DNS는 DNS-over-TCP로 변환한다.
- 설치 후 자동 연결은 opt-in이다.
- 자동 연결 대상은 사용자가 allowlist에 명시적으로 등록한 ADB serial만 해당한다.

### 1차 비목표

- ADB-free USB transport
- Android USB accessory 또는 custom USB framing protocol
- Android `VpnService` packet tunnel
- Wi-Fi Direct productization
- IPv6
- ICMP/ping
- arbitrary UDP
- QUIC/HTTP3
- production-grade TCP stack parity
- proxy authentication token for non-local bind
- Windows full-tunnel adapter
- system tray GUI 또는 desktop GUI
- 다른 장치에 인터넷을 공유하는 NAT/router mode

## 2. Data Path

1차 기본 데이터 경로는 다음이다.

```text
Linux app
  -> Linux kernel route
  -> pdalink0 TUN
  -> Linux TunHttpAdapter
  -> HTTP CONNECT to http://127.0.0.1:<local_port>
  -> adb forward tcp:<local_port> tcp:<android_proxy_port>
  -> Android 127.0.0.1:<android_proxy_port>
  -> Android HTTP proxy
  -> Android cellular network if available
  -> Android default network fallback if cellular is unavailable
  -> Internet
```

기본 포트는 다음이다.

```text
Linux local proxy: 127.0.0.1:18080
Android USB proxy: 127.0.0.1:8000
```

서비스 모드에서는 충돌 시 base port에서 `+1` fallback을 3회까지 허용한다.

```text
Linux local candidates: 18080, 18081, 18082, 18083
Android proxy candidates: 8000, 8001, 8002, 8003
```

Android remote port fallback과 Linux local port fallback은 독립적으로 처리한다.
예를 들어 Android가 `8001`을 선택하고 Linux가 `18080`을 선택하면 다음처럼
forward한다.

```text
adb forward tcp:18080 tcp:8001
```

선택된 실제 포트는 runtime state에 기록한다. Status와 cleanup은 항상 state의
실제 포트를 기준으로 동작한다. 다음 실행은 다시 base port부터 시도한다.

개발 및 진단 모드에서는 자동 port fallback을 끄고, 충돌을 명확한 실패로
보여줄 수 있어야 한다.

## 3. Android Design

### 3.1 Modes

Android service는 mode를 명확히 분리한다.

- `USB mode`: USB/ADB 경로 전용. Wi-Fi Direct를 만들지 않는다.
- `Wi-Fi Direct mode`: 기존 DIRECT-* group owner 경로. 1차 제품 경로가 아니다.
- `Debug/legacy mode`: 개발 중 필요한 경우 두 기능을 함께 사용할 수 있다.

ADB로 시작하는 기본 action은 USB mode를 시작한다.

```bash
adb shell am start-foreground-service \
  -n dev.pdalink.prototype/.ProxyService \
  -a dev.pdalink.prototype.START_USB
```

Stop action은 mode와 무관하게 service를 중지할 수 있어야 한다.

```bash
adb shell am start-foreground-service \
  -n dev.pdalink.prototype/.ProxyService \
  -a dev.pdalink.prototype.STOP
```

### 3.2 USB Mode Permissions

USB mode는 최소 권한 모드로 둔다.

USB mode 필수 권한:

- `INTERNET`
- `ACCESS_NETWORK_STATE`
- foreground service 권한
- notification 권한이 필요한 Android 버전에서는 notification 권한

Wi-Fi Direct 권한은 Wi-Fi Direct mode에서만 요청한다.

- `NEARBY_WIFI_DEVICES`
- `ACCESS_FINE_LOCATION` for older Android versions
- Wi-Fi Direct state/change permissions

Manifest의 `android.hardware.wifi.direct`는 USB mode 설치를 막지 않도록
`required=false`로 바꾸거나 제거한다.

### 3.3 Proxy Bind Address

USB mode의 Android proxy는 Android loopback에만 bind한다.

```text
127.0.0.1:<android_proxy_port>
```

Wi-Fi Direct mode에서만 `192.168.49.1` 또는 외부 interface bind를 허용한다.

USB mode 보안 전제는 다음이다.

- Android proxy는 Android localhost에만 노출된다.
- Linux proxy endpoint도 local-only endpoint로 사용된다.
- ADB authorization과 trusted device allowlist가 1차 신뢰 경계다.
- USB mode에서는 별도 proxy 인증 토큰을 넣지 않는다.

Non-local bind가 필요한 mode에는 후속 단계에서 token 또는 접근제어를 추가한다.

### 3.4 Egress Network

Android outbound socket은 cellular를 우선한다.

정책:

- Cellular network가 available이면 cellular `Network`를 사용한다.
- Cellular가 없거나 bind 실패 시 Android default network로 fallback한다.
- 상태에는 `egress=cellular` 또는 `egress=default fallback`을 명확히 표시한다.

Fallback을 허용하는 이유는 개발과 진단을 쉽게 하기 위해서다. 다만 사용자는
실제 트래픽이 cellular로 나가는지 default network로 나가는지 확인할 수 있어야 한다.

### 3.5 Android Status

Foreground notification은 짧은 상태만 보여준다.

예:

```text
PdaLink USB: starting
PdaLink USB: proxy 127.0.0.1:8000, egress cellular
PdaLink USB: proxy active, egress default
PdaLink USB: cellular unavailable
```

Notification을 탭하면 앱 상세 화면으로 이동한다.

앱 상세 화면은 다음을 보여준다.

- mode: USB / Wi-Fi Direct
- proxy bind address and port
- egress: cellular / default fallback
- active clients
- last request
- last error

USB mode notification에는 Wi-Fi Direct SSID/passphrase 같은 정보를 표시하지 않는다.

## 4. Linux User Experience

### 4.1 Primary Entrypoints

일반 사용자 기본 진입점은 watcher다.

```bash
./tools/pdalink.sh watch run
```

설치 후 자동 실행은 opt-in systemd user service로 제공한다.

```bash
./tools/pdalink.sh autostart enable
./tools/pdalink.sh autostart disable
./tools/pdalink.sh autostart status
```

하위 명령은 진단용으로 유지한다.

```bash
./tools/pdalink.sh usb start
./tools/pdalink.sh usb status
./tools/pdalink.sh usb stop
./tools/pdalink.sh system service start
./tools/pdalink.sh system service status
./tools/pdalink.sh system service stop
```

상태 확인은 top-level status로 모을 수 있어야 한다.

```bash
./tools/pdalink.sh status
./tools/pdalink.sh watch status
./tools/pdalink.sh device list
./tools/pdalink.sh system service logs
```

Desktop notification은 best-effort 옵션이다. `notify-send`가 있으면 연결,
해제, 오류를 알려주고, 없으면 조용히 skip한다. Headless Linux에서도 CLI만으로
완전히 동작해야 한다.

### 4.2 Autostart Model

자동 연결은 `systemd --user` service로 watcher를 실행하는 방식이다.

서비스 내용은 개념적으로 다음과 같다.

```text
ExecStart=<repo-or-install-path>/tools/pdalink.sh watch run
```

`udev` rule은 1차 기본 모델로 쓰지 않는다. ADB authorization, trusted device
판단, forward conflict, Android service start, proxy probe, cleanup 같은 상태
전이가 있기 때문에 장기 실행 watcher가 더 적합하다. 후속으로 udev는 watcher를
깨우는 보조 수단으로만 고려한다.

### 4.3 Auto Connect Policy

자동 연결 정책은 설정값으로 둔다.

```text
auto_connect = off | on_trusted_connect | when_offline
```

초기 설치 기본값:

```text
auto_connect = off
```

사용자가 autostart와 trusted device를 명시적으로 설정한 뒤의 권장 동작:

```text
auto_connect = on_trusted_connect
```

`on_trusted_connect`는 trusted Android가 연결되면 기존 Wi-Fi나 유선 인터넷이
있더라도 PdaLink 경로를 준비한다. 실제 route 우선순위는 `route_mode`가 결정한다.

`when_offline`은 옵션으로 남긴다. 단, 1차에서는 기존 인터넷의 실제 품질까지
판단하는 smart failover를 구현하지 않는다.

## 5. Trusted Device Allowlist

자동 연결은 ADB authorized device만으로는 부족하다. PdaLink에 명시적으로
등록된 Android device만 자동 연결 대상이 된다.

1차 device identity key:

```text
ADB serial
```

보조 정보로 다음을 저장할 수 있다.

- user-facing name
- model
- product
- last seen time
- default device 여부

예상 명령:

```bash
./tools/pdalink.sh device scan
./tools/pdalink.sh device trust --connected --name my-phone --default
./tools/pdalink.sh device trust R3CR406AAGE --name my-phone
./tools/pdalink.sh device untrust R3CR406AAGE
./tools/pdalink.sh device set-default R3CR406AAGE
./tools/pdalink.sh device list
```

정책:

- Installer나 autostart enable은 device를 자동 trust하지 않는다.
- `--connected`는 authorized device가 정확히 1대일 때만 허용한다.
- 여러 대가 연결되어 있으면 serial을 직접 요구한다.
- Trusted device가 1대만 연결되어 있으면 그 기기를 사용한다.
- Trusted device가 여러 대 연결되어 있고 default device가 있으면 default만 사용한다.
- Trusted device가 여러 대 연결되어 있고 default가 없으면 자동 연결하지 않는다.
- Untrusted authorized device에는 Android app start, ADB forward, route 변경을 수행하지 않는다.

Untrusted device 상태는 status/log에 다음처럼 표시한다.

```text
authorized but untrusted
```

## 6. Configuration And State

설정은 사용자별 XDG config에 저장한다.

```text
~/.config/pdalink/config.json
~/.config/pdalink/trusted-devices.json
```

권장 파일 권한:

```text
0600
```

Runtime state와 logs는 사용자별 XDG state에 저장한다.

```text
~/.local/state/pdalink/
```

개발 checkout에서는 기존 `.tools/runtime/`을 계속 사용할 수 있다.

Runtime state는 cleanup과 status의 근거가 되므로 다음 정보를 포함해야 한다.

- active device serial
- selected local port
- selected Android remote port
- created ADB forward identity
- TUN interface name
- TUN address
- routes added by PdaLink
- route mode
- DNS mode
- DNS changes applied by PdaLink
- process pid
- process start identity
- startup time
- last probe status
- last error

Root helper는 trusted device 판단을 하지 않는다. Trusted 판단은 일반 사용자
watcher가 수행하고, root helper는 watcher가 요청한 제한된 network operation만
수행한다.

## 7. Route Design

### 7.1 Route Modes

1차 route mode는 두 가지다.

```text
route_mode = active_override | fallback_low_priority
```

기본 권장 mode:

```text
active_override
```

### 7.2 Active Override

`active_override`는 PdaLink가 IPv4 인터넷 경로를 우선하도록 만든다.

기존 default route는 삭제하지 않는다. 대신 split default routes를 추가한다.

```bash
ip route replace 0.0.0.0/1 dev pdalink0
ip route replace 128.0.0.0/1 dev pdalink0
```

장점:

- 기존 default route를 지우지 않는다.
- PdaLink route가 더 구체적인 prefix라서 IPv4 인터넷 트래픽이 PdaLink로 간다.
- Cleanup은 `dev pdalink0`인 split routes만 제거하면 된다.

USB 1차 proxy endpoint는 `127.0.0.1:<local_port>`이므로 route loop가 없다.

기존 장기 TCP 연결은 split default route 전환 뒤 새 경로로 이관되지 않는다.
특히 이미 연결 중이던 `tailscaled` 제어 세션은 PdaLink가 최초 `SYN`을 보지 못했기
때문에 멈출 수 있다. 따라서 built-in `system-start`는 route와 DNS 적용 및 runtime
state 기록이 모두 성공한 뒤, `active_override`이고 `tailscaled.service`가 실행
중인 경우에만 `systemctl try-restart tailscaled.service` 호환성 훅을 실행한다.
비활성·미설치 상태와 `fallback_low_priority`에서는 실행하지 않으며,
`--no-tailscale-compat`로 명시적으로 끌 수 있다. 재시작 실패는 PdaLink route를
롤백하지 않고 경고로 기록한다.

### 7.3 Fallback Low Priority

`fallback_low_priority`는 PdaLink를 최하위 fallback route로 둔다.

Split default를 쓰지 않는다. 높은 metric의 일반 default route만 추가한다.

```bash
ip route replace default dev pdalink0 metric 50000
```

정책:

- 기존 유선/Wi-Fi default route가 있으면 기존 route가 우선한다.
- 기존 default route가 사라지면 PdaLink default route가 fallback으로 사용된다.
- DNS는 기본적으로 건드리지 않는다.
- Cleanup은 state에 기록된 PdaLink default route만 제거한다.

### 7.4 Smart Failover

Wi-Fi나 유선이 연결되어 있지만 실제 인터넷만 죽은 상황을 자동 감지해 PdaLink를
승격하는 기능은 1차 범위가 아니다.

후속 option:

```text
route_mode = smart_failover
```

Smart failover는 별도 설계가 필요하다.

- connectivity probe target
- 실패 횟수
- 복구 판정
- route promotion/demotion
- DNS policy
- 사용자 알림

## 8. DNS Policy

DNS는 route mode에 따라 다르게 관리한다.

### 8.1 Active Override DNS

`active_override`에서는 PdaLink가 주 인터넷 경로가 되므로 DNS도 함께 관리한다.

1차에서는 `systemd-resolved`만 자동 관리 대상으로 둔다.

적용 예:

```bash
resolvectl dns pdalink0 1.1.1.1 8.8.8.8
resolvectl domain pdalink0 '~.'
resolvectl default-route pdalink0 yes
```

Cleanup 예:

```bash
resolvectl revert pdalink0
```

`systemd-resolved`가 없으면 `/etc/resolv.conf`를 직접 덮어쓰지 않는다. 대신
status에 다음처럼 표시한다.

```text
DNS management unavailable
```

### 8.2 Fallback Low Priority DNS

`fallback_low_priority`에서는 기존 네트워크가 우선이므로 DNS를 기본적으로
변경하지 않는다.

별도 옵션으로만 PdaLink DNS 설정을 허용할 수 있다.

### 8.3 Resolv.conf

1차에서는 `/etc/resolv.conf`를 직접 수정하지 않는다.

이유:

- 배포판마다 NetworkManager, systemd-resolved, dhclient가 다르게 관리한다.
- 직접 파일을 쓰면 복구가 지저분해질 수 있다.
- 기존 시스템을 최대한 변경하지 않는다는 원칙과 맞지 않는다.

## 9. Privilege Boundary

Watcher는 일반 사용자 권한으로 실행한다.

일반 사용자 권한 작업:

- trusted device 판단
- ADB device scan
- Android debug activity start/stop
- ADB forward 생성과 제거
- proxy probe
- status/log 출력

Root 권한 작업:

- TUN interface open/create
- address 설정
- route 추가/삭제
- `resolvectl` per-link DNS 적용이 root 권한을 요구하는 환경

Root 작업은 좁은 root helper를 통해 수행한다.

정책:

- Installer가 root-owned helper를 설치한다.
- Sudoers rule은 PdaLink 전용 helper와 허용 subcommand로 제한한다.
- Runtime은 `sudo -n`을 사용한다.
- 비밀번호 prompt가 필요한 상태에서는 background에서 기다리지 않고 빠르게 실패한다.
- Codex나 agent는 sudo password를 받거나 저장하지 않는다.

## 10. Cleanup Policy

Cleanup은 보수적이어야 한다. 원칙은 다음이다.

```text
Only remove what PdaLink created and can prove it still owns.
```

자동 cleanup은 runtime state와 현재 시스템 상태가 일치할 때만 수행한다.

Cleanup 대상:

- PdaLink가 만든 ADB forward
- PdaLink가 만든 `pdalink0`
- PdaLink가 추가한 routes
- PdaLink가 적용한 per-link DNS
- PdaLink runtime state

Cleanup하지 않는 것:

- 다른 ADB forward
- 기존 default route
- 기존 DNS 설정
- firewall, iptables, nftables

검증 규칙:

- ADB forward 삭제는 serial, local tcp port, remote tcp port가 state와 일치할 때만 수행한다.
- Route 삭제는 route와 `dev pdalink0` 조건이 state와 일치할 때만 수행한다.
- DNS revert는 state에 PdaLink가 per-link DNS를 적용했다고 기록된 경우만 수행한다.
- State가 없거나 현재 상태가 다르면 자동 삭제하지 않고 `needs-manual-cleanup`으로 표시한다.

강제 cleanup은 별도 명령으로 분리한다.

```bash
./tools/pdalink.sh cleanup --force
```

### 10.1 Disconnect And Probe Failure

즉시 cleanup 조건:

- active trusted serial이 `adb devices`에서 사라짐

짧은 재확인 후 cleanup 조건:

- active trusted serial이 `offline`
- active trusted serial이 `unauthorized`

Proxy probe failure는 1회 실패로 cleanup하지 않는다.

권장 기본값:

```text
probe_interval = 2s
proxy_fail_threshold = 3
cleanup_after = about 6s continuous failure
startup_grace = 15s
```

Startup grace는 Android service 시작과 cellular bind 지연을 흡수하기 위한 것이다.

## 11. Protocol Support

### 11.1 TCP

1차 TCP 지원 범위는 일반적인 outbound TCP client best-effort다.

지원:

- outbound TCP connect
- 일반 HTTP/HTTPS client traffic
- package download, git, curl 같은 일반 사용 사례
- FIN/RST 기본 처리
- idle timeout cleanup

제한:

- 완전한 TCP retransmission 처리 없음
- out-of-order segment 재조립 없음
- 정교한 congestion control 없음
- inbound listen flow 없음
- 장시간 또는 고손실 connection은 best-effort

### 11.2 UDP

1차 UDP 지원은 DNS만이다.

지원:

- UDP destination port 53
- DNS-over-TCP conversion through Android HTTP CONNECT proxy
- DNS failure 시 UDP DNS `SERVFAIL` 응답

미지원:

- arbitrary UDP
- QUIC
- HTTP/3

DNS 외 UDP는 drop한다. 다만 verbose/debug status에서 목적지와 port, counter를
관찰할 수 있어야 한다.

### 11.3 IPv6

1차에서는 IPv6를 지원하지 않는다.

정책:

- IPv4 packet만 처리한다.
- IPv6 route를 추가하지 않는다.
- IPv6 packet은 drop하고 debug counter로 관찰 가능하게 한다.
- 문서와 status에 `IPv6 unsupported`를 명시한다.

IPv6 선호 환경에서는 일부 application이 먼저 IPv6를 시도하다가 지연될 수 있다.
1차에서는 이 현상을 limitation으로 둔다.

### 11.4 ICMP

ICMP와 ping은 1차에서 지원하지 않는다.

인터넷 검증은 `curl -4`와 DNS query smoke를 사용한다.

## 12. Installer And Setup

### 12.1 Install Command

`install`은 준비까지만 수행한다.

```bash
./tools/pdalink.sh install
```

Install이 하는 일:

- Linux prerequisite 확인
- TUN availability 확인
- repo-local Android tools bootstrap
- Android APK build
- authorized device가 있으면 APK install
- root helper와 sudoers rule 설치
- 다음 단계 안내

Install이 하지 않는 일:

- device 자동 trust
- autostart 자동 enable
- route 자동 변경
- DNS 설정 변경
- watcher 자동 시작

### 12.2 Setup Wizard

사람용 wizard를 제공한다.

```bash
./tools/pdalink.sh setup wizard
```

Wizard는 다음을 최소 입력으로 안내한다.

- sudo가 필요한 단계
- connected ADB devices 중 trust할 device 선택
- default device 지정 여부
- autostart enable 여부
- auto connect policy
- route mode
- DNS mode

### 12.3 Agent Mode For Codex

Codex 같은 agent가 설치를 돕기 쉽도록 machine-readable plan과 non-interactive
apply를 제공한다.

```bash
./tools/pdalink.sh setup plan --json
./tools/pdalink.sh setup apply \
  --trust R3CR406AAGE \
  --name my-phone \
  --default \
  --autostart enable \
  --auto-connect on_trusted_connect \
  --route-mode active_override
```

Agent mode 원칙:

- 필요한 사용자 입력을 JSON plan에 명확히 드러낸다.
- Sudo password는 agent가 받거나 저장하지 않는다.
- ADB authorization이 필요하면 사용자가 휴대폰에서 승인해야 한다고 출력한다.
- 여러 device 선택이 필요하면 agent가 임의 선택하지 않고 사용자 선택을 요구한다.
- Non-interactive apply는 명시 옵션이 없으면 trust/autostart를 수행하지 않는다.

## 13. Status Model

Status output은 다음을 보여야 한다.

- autostart enabled/disabled
- watcher running/stopped
- configured `auto_connect`
- configured `route_mode`
- connected ADB devices
- trusted/default 여부
- active device serial
- Android proxy mode
- selected Android proxy port
- selected Linux local port
- proxy probe result
- TUN interface state
- route state
- DNS mode and state
- unsupported protocol counters
- last error
- cleanup state
- next command

Status는 사람이 읽는 출력과 JSON 출력을 모두 지원하는 것이 좋다.

```bash
./tools/pdalink.sh status
./tools/pdalink.sh status --json
```

## 14. Release Gate

자동 테스트:

```bash
python3 -m unittest discover -s tests
bash -n tools/*.sh
./tools/pdalink.sh setup plan --json
./tools/pdalink.sh device scan
./tools/pdalink.sh watch status
```

실기기 gate:

- trusted Android 1대 등록
- `autostart enable`
- USB 연결 시 watcher가 trusted serial을 선택
- Android USB mode proxy 시작
- Android proxy가 `127.0.0.1:<android_proxy_port>`에 bind
- ADB forward 생성
- proxy PAC 또는 health endpoint probe 성공
- `pdalink0` 생성
- `active_override` route 검증
- `fallback_low_priority` route 검증
- no-proxy `curl -4 http://1.1.1.1/cdn-cgi/trace` 성공
- DNS 기반 `curl -4 https://ifconfig.me` 또는 동등 테스트 성공
- 실행 중이던 Tailscale이 `active_override` 전환 뒤 새 제어 연결과 DERP 경로로 복구
- USB 분리 시 route/DNS/forward cleanup 성공
- cleanup 후 기존 인터넷 경로 정상
- cleanup 후 stale state 없음

## 15. Implementation Deltas From Current Repo

현재 repo는 1차 prototype 기반이 이미 있다. 이 설계를 제품화하려면 다음 차이를
해소해야 한다.

- Android `ProxyService`가 USB 시작 시 Wi-Fi Direct를 만들지 않도록 mode 분리.
- Android USB mode proxy를 all-interface bind가 아니라 `127.0.0.1` bind로 변경.
- Android action을 `START_USB`, `START_WIFI_DIRECT`, `STOP`, `REFRESH` 등으로 명확히 분리.
- Android Wi-Fi Direct permission request를 Wi-Fi Direct mode로 제한.
- Manifest의 Wi-Fi Direct feature requirement를 USB mode 설치를 막지 않게 변경.
- Android notification에 USB mode와 egress 상태를 표시.
- Linux trusted device allowlist 구현.
- XDG config/state 저장 구현.
- Top-level `status`, `device`, `autostart`, `config`, `setup` commands 추가.
- `systemd --user` autostart unit 설치/관리 추가.
- Route mode를 `active_override`와 `fallback_low_priority`로 분리.
- DNS mode에서 `systemd-resolved` per-link 설정과 cleanup 추가.
- Runtime state ownership 검증을 강화.
- Service mode port fallback과 state 기록 추가.
- Unsupported UDP/IPv6 counters와 status 노출 추가.
- Setup wizard와 Codex agent mode 추가.

## 16. Future Work

후속 단계로 분리한 항목:

- ADB-free USB transport
- Android USB accessory/custom USB protocol
- smart failover
- IPv6 support
- ICMP/ping support
- arbitrary UDP / QUIC / HTTP3
- stronger TCP behavior
- proxy authentication token for non-local bind
- Wi-Fi Direct productization
- Windows full-tunnel adapter
- system tray GUI / desktop GUI
- NAT/router mode for sharing Linux-side internet to other devices
