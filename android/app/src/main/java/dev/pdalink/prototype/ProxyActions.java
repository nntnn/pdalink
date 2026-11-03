package dev.pdalink.prototype;

final class ProxyActions {
    static final String ACTION_START = "dev.pdalink.prototype.START";
    static final String ACTION_START_USB = "dev.pdalink.prototype.START_USB";
    static final String ACTION_STOP_USB = "dev.pdalink.prototype.STOP_USB";
    static final String ACTION_START_PRODUCT_USB = "dev.pdalink.prototype.START_PRODUCT_USB";
    static final String ACTION_START_WIFI_DIRECT = "dev.pdalink.prototype.START_WIFI_DIRECT";
    static final String ACTION_INVITE_WIFI_DIRECT_PEER =
            "dev.pdalink.prototype.INVITE_WIFI_DIRECT_PEER";
    static final String ACTION_STOP_WIFI_DIRECT = "dev.pdalink.prototype.STOP_WIFI_DIRECT";
    static final String ACTION_START_LOCAL_HOTSPOT = "dev.pdalink.prototype.START_LOCAL_HOTSPOT";
    static final String ACTION_STOP_LOCAL_HOTSPOT = "dev.pdalink.prototype.STOP_LOCAL_HOTSPOT";
    static final String ACTION_QUERY_LOCAL_HOTSPOT =
            "dev.pdalink.prototype.QUERY_LOCAL_HOTSPOT";
    static final String ACTION_STOP_WIFI = "dev.pdalink.prototype.STOP_WIFI";
    static final String ACTION_STOP = "dev.pdalink.prototype.STOP";
    static final String ACTION_REFRESH = "dev.pdalink.prototype.REFRESH";
    static final String ACTION_STATUS = "dev.pdalink.prototype.STATUS";
    static final String EXTRA_STATUS = "status";
    static final String EXTRA_PROXY_STATUS = "proxy_status";
    static final String EXTRA_PROXY_PORT = "proxy_port";
    static final String EXTRA_USB_PROXY_STATUS = "usb_proxy_status";
    static final String EXTRA_WIFI_PROXY_STATUS = "wifi_proxy_status";
    static final String EXTRA_MODE = "mode";
    static final String EXTRA_EGRESS = "egress";
    static final String EXTRA_GROUP_SSID = "group_ssid";
    static final String EXTRA_GROUP_PASSPHRASE = "group_passphrase";
    static final String EXTRA_GROUP_INTERFACE = "group_interface";
    static final String EXTRA_WIFI_GROUP_OWNED = "wifi_direct_group_owned";
    static final String EXTRA_WIFI_GROUP_CLIENT_COUNT = "wifi_direct_group_client_count";
    static final String EXTRA_WIFI_GROUP_CLIENT_ADDRESS =
            "wifi_direct_group_client_address";
    static final String EXTRA_WIFI_TRANSPORT = "wifi_transport";
    static final String EXTRA_WIFI_SECURITY = "wifi_security";
    static final String EXTRA_WIFI_BAND = "wifi_direct_band";
    static final String EXTRA_WIFI_FREQUENCY_MHZ = "wifi_direct_frequency_mhz";
    static final String EXTRA_WIFI_P2P_PEER = "wifi_direct_p2p_peer";
    static final String EXTRA_WIFI_P2P_RENEGOTIATE = "wifi_direct_p2p_renegotiate";
    static final String EXTRA_LINUX_TARGET = "linux_target";
    static final String EXTRA_USB_TARGET = "usb_target";
    static final String EXTRA_WIFI_TARGET = "wifi_target";
    static final String EXTRA_LAST_REQUEST = "last_request";
    static final String EXTRA_LAST_ERROR = "last_error";
    static final String EXTRA_CLIENT_COUNT = "client_count";
    static final String EXTRA_TRAFFIC_UP_BYTES = "traffic_up_bytes";
    static final String EXTRA_TRAFFIC_DOWN_BYTES = "traffic_down_bytes";
    static final String EXTRA_PRODUCT_USB_STATUS = "product_usb_status";
    static final String EXTRA_SESSION_TRANSPORTS = "session_transports";
    static final String EXTRA_SESSION_CLIENT_ADDRESSES = "session_client_addresses";
    static final String EXTRA_SESSION_METHODS = "session_methods";
    static final String EXTRA_SESSION_DESTINATIONS = "session_destinations";
    static final String EXTRA_SESSION_STARTED_AT = "session_started_at";
    static final String EXTRA_SESSION_ENDED_AT = "session_ended_at";
    static final String EXTRA_SESSION_UP_BYTES = "session_up_bytes";
    static final String EXTRA_SESSION_DOWN_BYTES = "session_down_bytes";
    static final String EXTRA_SESSIONS_TRUNCATED = "sessions_truncated";

    static final String MODE_USB = "USB";
    static final String MODE_PRODUCT_USB = "Product USB";
    static final String MODE_WIFI_DIRECT = "Wi-Fi Direct";
    static final String MODE_LOCAL_HOTSPOT = "Local Hotspot";
    static final String MODE_USB_WIFI_DIRECT = "USB + Wi-Fi Direct";
    static final String MODE_USB_LOCAL_HOTSPOT = "USB + Local Hotspot";

    private ProxyActions() {
    }
}
