// main/apps/voice/udp_audio.c —— Wi-Fi UDP 音频/事件通道实现。
// 协议契约见 docs/voice-udp-channel.md(与 companion/udp_transport.py 同构)。
#include "udp_audio.h"

#include "app_protocol.h"
#include "nvs_settings.h"
#include "cJSON.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lwip/inet.h"
#include "lwip/netdb.h"
#include "lwip/sockets.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

static const char *TAG = "udp_audio";

// 一个音频块固定 804B(4B 块头 + 800B 4bit 数据),见 apps/voice/adpcm.h
#define ADPCM_BLOCK_MAX 804
// 上行缓冲:[type 1B][payload] + 可选 4B 令牌尾(v2 跨网)
#define TX_BUF_MAX (1 + 2 + ADPCM_BLOCK_MAX + 4)
// 下行接收缓冲:最大 payload = 513B(EVENT) + 1B type + 4B 令牌尾,留余量以识别异常大包
#define RX_BUF_MAX 640
// 对端"消失过又回来"的判定阈值。注意:这里的 *1000 是 ms→us 换算,结果就是
// 链路超时本身(3000ms = 3e6us),并非注释旧文所写的"3 倍"。语义上正好:
// beacon 正常时每秒一发(间隔 ~1s < 3s 不触发),一旦停了 3s 以上说明链路已断,
// 下一发 beacon 就重发 device.hello 让 companion 重新学到我们。
#define PEER_STALE_US ((int64_t)VOICE_UDP_PEER_TIMEOUT_MS * 1000)
#define MY_IP_STR(buf) ((buf)[0] ? (buf) : "0.0.0.0")

static int s_sock = -1;
static volatile bool s_active = false;
static TaskHandle_t s_rx_task = NULL;
static SemaphoreHandle_t s_tx_mux = NULL;

// 已学到的对端地址(只有 RX 任务在收到 beacon 时写;s_peer_mux 保护)
static struct sockaddr_in s_peer;
static volatile bool s_peer_known = false;
static volatile int64_t s_peer_last_us = 0;
static portMUX_TYPE s_peer_mux = portMUX_INITIALIZER_UNLOCKED;

// 上行缓冲(tx_send 内持有 s_tx_mux 时使用)
static uint8_t s_tx_buf[TX_BUF_MAX];
// 音频组装缓冲:唯一写者是音频发送 worker,故无需加锁
static uint8_t s_audio_pkt[2 + ADPCM_BLOCK_MAX];
static uint8_t s_audio_seq = 0;

static portMUX_TYPE s_stat_mux = portMUX_INITIALIZER_UNLOCKED;
static uint32_t s_audio_drops = 0;
static uint32_t s_event_drops = 0;
static volatile uint32_t s_beacon_cnt = 0;   // 链路诊断:收到 beacon 总数
static volatile uint32_t s_ping_cnt = 0;     // 链路诊断:发出 ping 总数

// ---- 跨网直连状态(v2;只在 udp_audio_init 与 RX 任务上下文读写,无锁) ----
static char     s_static_host[NVS_UDP_HOST_MAX];   // 空=未配置(纯局域网)
static uint16_t s_static_port = 33333;
static char     s_static_token[NVS_UDP_TOKEN_MAX]; // 空=令牌关闭(遗留模式)
static uint8_t  s_token4[4];                       // FNV-1a(token) 校验尾
static bool     s_token_on = false;
static uint32_t s_static_resolved = 0;             // 已解析的 IPv4(网络序)
static int64_t  s_last_resolve_us = 0;
static int64_t  s_last_static_try_us = 0;
#define STATIC_TRY_MS      3000u    // 无对端时向静态地址发 hello 的周期
#define RESOLVE_RETRY_US   (60LL * 1000000)   // 域名重解析周期(缓存解析结果)

// FNV-1a 32bit —— 与 companion/udp_transport.py 的 _fnv1a4 逐字节一致
static void token_fnv1a4(const char *token, uint8_t out[4]) {
    uint32_t h = 0x811C9DC5u;
    for (const char *p = token; *p; p++) {
        h ^= (uint8_t)*p;
        h *= 0x01000193u;
    }
    out[0] = (uint8_t)(h & 0xFF);        // little-endian, 与 python to_bytes 一致
    out[1] = (uint8_t)((h >> 8) & 0xFF);
    out[2] = (uint8_t)((h >> 16) & 0xFF);
    out[3] = (uint8_t)((h >> 24) & 0xFF);
}

// ---- 内部工具 ----

static void drop_inc(uint32_t *counter)
{
    portENTER_CRITICAL(&s_stat_mux);
    (*counter)++;
    portEXIT_CRITICAL(&s_stat_mux);
}

static uint32_t drop_get(uint32_t *counter)
{
    portENTER_CRITICAL(&s_stat_mux);
    uint32_t v = *counter;
    portEXIT_CRITICAL(&s_stat_mux);
    return v;
}

// 取对端地址快照。返回 false = 尚未学到对端(链路未 up)。
static bool peer_snapshot(struct sockaddr_in *out)
{
    bool known;
    portENTER_CRITICAL(&s_peer_mux);
    known = s_peer_known;
    if (known) *out = s_peer;
    portEXIT_CRITICAL(&s_peer_mux);
    return known;
}

// 链路是否新鲜:对端已知且最近 VOICE_UDP_PEER_TIMEOUT_MS 内收到过对端报文。
static bool peer_fresh(void)
{
    int64_t now = esp_timer_get_time();
    bool fresh = false;
    portENTER_CRITICAL(&s_peer_mux);
    fresh = s_peer_known && s_peer_last_us != 0 &&
            (now - s_peer_last_us) < (int64_t)VOICE_UDP_PEER_TIMEOUT_MS * 1000;
    portEXIT_CRITICAL(&s_peer_mux);
    return fresh;
}

// 单播一发。对端未知 / socket 未就绪 / 发送失败返回 -1。
// s_tx_buf 为共享缓冲,由 s_tx_mux 串行化(audio worker 与 app task 是两个任务)。
static int tx_send(uint8_t type, const void *payload, size_t len)
{
    if (s_sock < 0 || len + 1 > TX_BUF_MAX || s_tx_mux == NULL) {
        return -1;
    }
    if (xSemaphoreTake(s_tx_mux, pdMS_TO_TICKS(50)) != pdTRUE) {
        ESP_LOGW(TAG, "TX 互斥等待超时,丢弃该帧");
        return -1;
    }
    int rc = -1;
    struct sockaddr_in peer;
    if (peer_snapshot(&peer)) {
        s_tx_buf[0] = type;
        if (len > 0) memcpy(s_tx_buf + 1, payload, len);
        size_t total = len + 1;
        if (s_token_on) {              // v2: 上行包尾部追加 4B 令牌校验
            memcpy(s_tx_buf + total, s_token4, 4);
            total += 4;
        }
        ssize_t n = sendto(s_sock, s_tx_buf, total, 0,
                           (const struct sockaddr *)&peer, sizeof(peer));
        if (n == (ssize_t)total) {
            rc = 0;
        } else {
            ESP_LOGW(TAG, "sendto 失败: rc=%d errno=%d", (int)n, errno);
        }
    }
    xSemaphoreGive(s_tx_mux);
    return rc;
}

// 向静态地址直发一发 device.hello(不经 s_peer —— 跨网首次建链就是靠它让
// companion 从 recvfrom 学到我们)。host 是域名时 getaddrinfo 阻塞解析,
// 只发生在链路建立期(RX 任务上下文),结果缓存 RESOLVE_RETRY_US。
static void static_send_hello(void)
{
    if (!s_static_host[0] || s_sock < 0) return;

    int64_t now = esp_timer_get_time();
    if (s_static_resolved == 0 ||
        now - s_last_resolve_us > RESOLVE_RETRY_US) {
        char portstr[6];
        snprintf(portstr, sizeof(portstr), "%u", (unsigned)s_static_port);
        struct addrinfo hint = {0};
        hint.ai_family = AF_INET;
        hint.ai_socktype = SOCK_DGRAM;
        struct addrinfo *res = NULL;
        if (getaddrinfo(s_static_host, portstr, &hint, &res) != 0 || !res) {
            ESP_LOGW(TAG, "静态对端解析失败: %s:%u(下次再试)",
                     s_static_host, (unsigned)s_static_port);
            s_static_resolved = 0;
        } else {
            s_static_resolved =
                ((struct sockaddr_in *)res->ai_addr)->sin_addr.s_addr;
            freeaddrinfo(res);
            ESP_LOGI(TAG, "静态对端已解析: %s → %s:%u", s_static_host,
                     inet_ntoa(*(struct in_addr *)&s_static_resolved),
                     (unsigned)s_static_port);
        }
        s_last_resolve_us = now;
    }
    if (s_static_resolved == 0) return;

    char buf[128];
    size_t n = app_protocol_device_hello(buf, sizeof(buf), 1);
    if (n == 0) return;
    uint8_t pkt[1 + 128 + 4];
    size_t total = 0;
    pkt[total++] = VOICE_UDP_T_EVENT;
    memcpy(pkt + total, buf, n);
    total += n;
    if (s_token_on) {
        memcpy(pkt + total, s_token4, 4);
        total += 4;
    }
    struct sockaddr_in dst = {0};
    dst.sin_family = AF_INET;
    dst.sin_addr.s_addr = s_static_resolved;
    dst.sin_port = htons(s_static_port);
    ssize_t rc = sendto(s_sock, pkt, total, 0,
                        (struct sockaddr *)&dst, sizeof(dst));
    if (rc != (ssize_t)total) {
        ESP_LOGW(TAG, "静态 hello sendto 失败: rc=%d errno=%d", (int)rc, errno);
    }
}

void udp_audio_apply_link_cfg(const char *host, uint16_t port, const char *token)
{
    // 调用上下文: udp_audio_init(RX 任务启动前)或 RX 任务内(cfg.udp 下行),
    // 无并发写者。host/token 越界由调用方(cJSON 提取时)保证 ≤ 上限。
    strncpy(s_static_host, host ? host : "", sizeof(s_static_host) - 1);
    s_static_host[sizeof(s_static_host) - 1] = '\0';
    s_static_port = port ? port : 33333;
    strncpy(s_static_token, token ? token : "", sizeof(s_static_token) - 1);
    s_static_token[sizeof(s_static_token) - 1] = '\0';
    s_token_on = s_static_token[0] != '\0';
    if (s_token_on) token_fnv1a4(s_static_token, s_token4);
    s_static_resolved = 0;         // 配置变化 → 重置解析缓存
    s_last_resolve_us = 0;
    s_last_static_try_us = 0;
    ESP_LOGI(TAG, "link cfg: host='%s' port=%u token=%s",
             s_static_host, (unsigned)s_static_port,
             s_token_on ? "on" : "off");
}

// 下行 cfg.udp: 存 NVS 并热生效。返回 true = 本帧已处理(不再进事件系统)。
static bool handle_cfg_udp(const char *json, size_t len)
{
    if (!strstr(json, "cfg.udp")) return false;   // 快速预筛(微秒级)
    cJSON *root = cJSON_ParseWithLength(json, len);
    if (!root) return false;
    const cJSON *type = cJSON_GetObjectItemCaseSensitive(root, "type");
    if (!cJSON_IsString(type) ||
        strcmp(type->valuestring, "cfg.udp") != 0) {
        cJSON_Delete(root);
        return false;
    }
    const cJSON *host_j = cJSON_GetObjectItemCaseSensitive(root, "host");
    const cJSON *port_j = cJSON_GetObjectItemCaseSensitive(root, "port");
    const cJSON *tok_j  = cJSON_GetObjectItemCaseSensitive(root, "token");
    char host[NVS_UDP_HOST_MAX] = "";
    char token[NVS_UDP_TOKEN_MAX] = "";
    if (cJSON_IsString(host_j) && strlen(host_j->valuestring) < sizeof(host)) {
        strcpy(host, host_j->valuestring);
    }
    if (cJSON_IsString(tok_j) && strlen(tok_j->valuestring) < sizeof(token)) {
        strcpy(token, tok_j->valuestring);
    }
    uint16_t port = 33333;
    if (cJSON_IsNumber(port_j) && port_j->valuedouble >= 1 &&
        port_j->valuedouble <= 65535) {
        port = (uint16_t)port_j->valuedouble;
    }
    cJSON_Delete(root);

    if (host[0] == '\0') {         // host 为空 = 清除跨网配置(回纯局域网)
        (void)nvs_settings_set_udp_host("");
        ESP_LOGI(TAG, "cfg.udp: 已清除静态对端");
    } else {
        (void)nvs_settings_set_udp_host(host);
        (void)nvs_settings_set_udp_port(port);
        (void)nvs_settings_set_udp_token(token);
        udp_audio_apply_link_cfg(host, port, token);
        ESP_LOGI(TAG, "cfg.udp: 跨网配置已存 NVS 并热生效(%s:%u)", host,
                 (unsigned)port);
        static_send_hello();       // 立即让 PC 学到我们(不等下一周期)
    }
    return true;
}

static void send_device_hello(void)
{
    char buf[128];
    size_t n = app_protocol_device_hello(buf, sizeof(buf), 1);
    if (n > 0) {
        udp_audio_notify_event(buf, n);
    }
}

// 判活:每 VOICE_UDP_PING_MS 给 companion 一发空载荷 PING。
// 设备空闲时不产生任何报文,companion 就分不清"设备还在"和"设备退出了语音应用"
// —— 这一发是它 6s 静默判断开的唯一依据。链路未通时不发(省得白算)。
// 不计入丢帧统计:判活失败由 beacon/下次报文自然覆盖,不是音频/事件语义的丢失。
static void send_liveness_ping(void)
{
    if (!peer_fresh()) return;
    (void)tx_send(VOICE_UDP_T_PING, NULL, 0);
    s_ping_cnt++;
}

// beacon:建立或刷新对端。返回 true = 需要回一发 device.hello。
static bool peer_accept_beacon(const struct sockaddr_in *src)
{
    int64_t now = esp_timer_get_time();
    bool need_hello = false;
    portENTER_CRITICAL(&s_peer_mux);
    bool same = s_peer_known && s_peer.sin_addr.s_addr == src->sin_addr.s_addr;
    if (!same) {
        s_peer.sin_family = AF_INET;
        s_peer.sin_addr   = src->sin_addr;
        s_peer.sin_port   = htons(VOICE_UDP_PORT);
        s_peer_known      = true;
        need_hello        = true;
    } else if (s_peer_last_us == 0 || now - s_peer_last_us > PEER_STALE_US) {
        need_hello = true;   // 对端消失过又回来:让 companion 重新学到我们
    }
    s_peer_last_us = now;
    portEXIT_CRITICAL(&s_peer_mux);
    return need_hello;
}

// 非 beacon 的下行:仅在与已建立对端同源时续命(防第三方刷报文续活链路)
static void peer_refresh(const struct sockaddr_in *src)
{
    int64_t now = esp_timer_get_time();
    portENTER_CRITICAL(&s_peer_mux);
    if (s_peer_known && s_peer.sin_addr.s_addr == src->sin_addr.s_addr) {
        s_peer_last_us = now;
    }
    portEXIT_CRITICAL(&s_peer_mux);
}

static void rx_task(void *arg)
{
    (void)arg;
    static uint8_t rx[RX_BUF_MAX];
    static char json[APP_PROTO_RX_CAP + 1];
    int64_t last_ping_us = 0;
    ESP_LOGI(TAG, "UDP RX task started (port %d)", VOICE_UDP_PORT);

    while (s_active) {
        struct sockaddr_in src;
        socklen_t slen = sizeof(src);
        ssize_t n = recvfrom(s_sock, rx, sizeof(rx), 0,
                             (struct sockaddr *)&src, &slen);
        // n <= 0 是 SO_RCVTIMEO(200ms)到期,正常路径 → 直接落到下面的判活节拍
        if (n > 0) {
            const uint8_t type = rx[0];
            if (type == VOICE_UDP_T_BEACON) {
                // v2: 配了令牌时校验 beacon JSON 里的 "t" 字段(防公网伪造 beacon
                // 劫持对端地址)。未配令牌 = 局域网遗留模式, 放行。
                if (s_token_on) {
                    char expect[64];
                    snprintf(expect, sizeof(expect), "\"t\":\"%s\"",
                             s_static_token);
                    // beacon 很小,拷到 NUL 结尾缓冲用 strstr(memmem 是 GNU
                    // 扩展,newlib 不保证有,不赌)
                    char bj[256];
                    size_t blen = (size_t)n - 1;
                    if (blen > sizeof(bj) - 1) blen = sizeof(bj) - 1;
                    memcpy(bj, rx + 1, blen);
                    bj[blen] = '\0';
                    if (!strstr(bj, expect)) {
                        ESP_LOGW(TAG, "丢弃无令牌 beacon 来自 %s",
                                 inet_ntoa(src.sin_addr));
                        continue;
                    }
                }
                s_beacon_cnt++;
                if (peer_accept_beacon(&src)) {
                    ESP_LOGI(TAG, "companion beacon 来自 %s → link up",
                             inet_ntoa(src.sin_addr));
                    send_device_hello();
                }
            } else if (type == VOICE_UDP_T_EVENT) {
                struct sockaddr_in peer;
                if (!peer_snapshot(&peer) || peer.sin_addr.s_addr != src.sin_addr.s_addr ||
                    src.sin_port != peer.sin_port) continue;
                peer_refresh(&src);
                size_t plen = (size_t)n - 1;
                if (plen > APP_PROTO_RX_CAP) plen = APP_PROTO_RX_CAP;   // 截断防御
                memcpy(json, rx + 1, plen);
                json[plen] = '\0';
                if (handle_cfg_udp(json, plen)) {   // 跨网配置下行:NVS+热生效
                    continue;
                }
                app_event_t ev;
                if (app_protocol_parse(json, plen, &ev)) {
                    app_protocol_dispatch_event(&ev);   // 审批重要投递,其余零阻塞
                } else {
                    ESP_LOGW(TAG, "下行 JSON 拒绝: %.*s",
                             (int)(plen > 80 ? 80 : plen), json);
                }
            } else {
                ESP_LOGW(TAG, "未知报文类型 0x%02x(%d B),忽略", type, (int)n);
            }
        }

        // 取当前时刻(静态直发节拍与判活节拍共用)
        int64_t now_us = esp_timer_get_time();

        // 静态直发节拍: 配了跨网对端且当前无活对端时,周期性直发 hello
        // (companion 从 recvfrom 学到我们后回 beacon,链路照常建立)。
        if (s_static_host[0] && !peer_fresh() &&
            (s_last_static_try_us == 0 ||
             now_us - s_last_static_try_us >=
                 (int64_t)STATIC_TRY_MS * 1000)) {
            s_last_static_try_us = now_us;
            static_send_hello();
        }

        // 判活节拍:与收包解耦(这一轮有没有报文都要跑)
        if (last_ping_us == 0 ||
            now_us - last_ping_us >= (int64_t)VOICE_UDP_PING_MS * 1000) {
            last_ping_us = now_us;
            send_liveness_ping();
        }
        // 链路诊断(每 5s):beacon/ping 计数不涨的一侧就是断点
        static int64_t last_diag_us = 0;
        if (last_diag_us == 0 ||
            now_us - last_diag_us >= 5000000) {
            last_diag_us = now_us;
            bool known;
            int64_t age_ms;
            portENTER_CRITICAL(&s_peer_mux);
            known = s_peer_known;
            age_ms = s_peer_last_us ? (now_us - s_peer_last_us) / 1000 : -1;
            portEXIT_CRITICAL(&s_peer_mux);
            ESP_LOGI(TAG, "link diag: beacons=%u pings=%u peer_known=%d peer_age_ms=%ld",
                     (unsigned)s_beacon_cnt, (unsigned)s_ping_cnt,
                     (int)known, (long)age_ms);
        }
    }
    ESP_LOGI(TAG, "UDP RX task exiting");
    s_rx_task = NULL;
    vTaskDelete(NULL);
}

// ---- 公开接口 ----

int udp_audio_init(void)
{
    if (s_sock >= 0) return 0;   // 幂等

    if (s_tx_mux == NULL) {
        s_tx_mux = xSemaphoreCreateMutex();
        if (s_tx_mux == NULL) {
            ESP_LOGE(TAG, "TX 互斥创建失败");
            return -1;
        }
    }

    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGE(TAG, "socket 创建失败: errno=%d", errno);
        return -1;
    }
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port        = htons(VOICE_UDP_PORT);
    if (bind(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        ESP_LOGE(TAG, "bind %d 失败: errno=%d", VOICE_UDP_PORT, errno);
        close(sock);
        return -1;
    }
    // 接收超时:让 RX 任务能周期性复查停止位(deinit 才能干净退出)
    struct timeval tv;
    tv.tv_sec  = 0;
    tv.tv_usec = 200 * 1000;
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
    // 允许发往广播地址(设备当前不发广播,保留给将来的主动探测)
    int bcast = 1;
    setsockopt(sock, SOL_SOCKET, SO_BROADCAST, &bcast, sizeof(bcast));

    s_sock = sock;
    s_active = true;
    // 跨网配置(NVS)装载: PC 经 cfg.udp 下行写入的 静态对端+令牌。
    // 在家模式 host 为空 → 静态直发节拍自动休眠, 行为与 v1 完全一致。
    {
        char host[NVS_UDP_HOST_MAX];
        char token[NVS_UDP_TOKEN_MAX];
        uint16_t port = 33333;
        if (nvs_settings_get_udp_host(host, sizeof(host)) == ESP_OK &&
            nvs_settings_get_udp_port(&port) == ESP_OK &&
            nvs_settings_get_udp_token(token, sizeof(token)) == ESP_OK &&
            host[0]) {
            udp_audio_apply_link_cfg(host, port, token);
            ESP_LOGI(TAG, "跨网模式: 静态对端 %s:%u(token=%s)",
                     host, (unsigned)port, token[0] ? "on" : "off");
        }
    }
    if (xTaskCreate(rx_task, "udp_rx", 4096, NULL, 4, &s_rx_task) != pdPASS) {
        ESP_LOGE(TAG, "RX 任务创建失败");
        s_active = false;
        close(sock);
        s_sock = -1;
        return -1;
    }
    ESP_LOGI(TAG, "UDP 通道就绪(port %d),等待 companion beacon", VOICE_UDP_PORT);
    return 0;
}

int udp_audio_deinit(void)
{
    if (s_sock < 0 && s_rx_task == NULL) return 0;   // 幂等

    // 1. 先置停止位:recvfrom 的 200ms 超时会让任务回到循环顶看到它
    s_active = false;
    // 2. 等任务自己退出再关 socket —— close 与阻塞中的 recvfrom 并发是竞态
    for (int i = 0; i < 50 && s_rx_task != NULL; i++) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (s_rx_task != NULL) {
        ESP_LOGW(TAG, "RX 任务未在 500ms 内退出(继续关闭,任务将自行收敛)");
    }
    // 3. 关 socket
    if (s_sock >= 0) {
        close(s_sock);
        s_sock = -1;
    }
    // 4. 清对端状态:下次进入重新发现
    portENTER_CRITICAL(&s_peer_mux);
    s_peer_known  = false;
    s_peer_last_us = 0;
    memset(&s_peer, 0, sizeof(s_peer));
    portEXIT_CRITICAL(&s_peer_mux);
    s_audio_seq = 0;
    ESP_LOGI(TAG, "UDP 通道已关闭");
    return 0;
}

int udp_audio_notify_audio(const uint8_t *frame, size_t len)
{
    if (frame == NULL || len == 0 || len > ADPCM_BLOCK_MAX) return -1;
    // UDP 下每块恒为单片且是末片 → 帧头 [seq][0x80],与 companion 的
    // reassemble_adpcm() 期望的"新 seq + is_last"完全一致。
    s_audio_pkt[0] = s_audio_seq++;
    s_audio_pkt[1] = 0x80;
    memcpy(s_audio_pkt + 2, frame, len);
    int rc = tx_send(VOICE_UDP_T_AUDIO, s_audio_pkt, len + 2);
    if (rc != 0) drop_inc(&s_audio_drops);
    return rc;
}

int udp_audio_notify_event(const char *line, size_t len)
{
    if (line == NULL || len == 0) return -1;
    int rc = tx_send(VOICE_UDP_T_EVENT, line, len);
    if (rc != 0) drop_inc(&s_event_drops);
    return rc;
}

int udp_audio_notify_event_blocking(const char *line, size_t len, uint32_t timeout_ms)
{
    (void)timeout_ms;   // UDP 无发送队列与背压,无等待语义
    return udp_audio_notify_event(line, len);
}

bool udp_audio_connected(void)        { return peer_fresh(); }
bool udp_audio_event_subscribed(void) { return peer_fresh(); }

uint32_t udp_audio_audio_drops(void) { return drop_get(&s_audio_drops); }
uint32_t udp_audio_event_drops(void) { return drop_get(&s_event_drops); }

const char *udp_audio_peer_ip(void)
{
    static char buf[16];
    struct sockaddr_in peer;
    if (!peer_snapshot(&peer)) {
        return "0.0.0.0";
    }
    const char *s = inet_ntoa(peer.sin_addr);
    strncpy(buf, s ? s : "0.0.0.0", sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';
    return MY_IP_STR(buf);
}
