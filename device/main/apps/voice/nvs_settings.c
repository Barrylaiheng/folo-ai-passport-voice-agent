// main/apps/voice/nvs_settings.c —— 设置存储实现。
#include "nvs_settings.h"
#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"
#include <string.h>

static const char *TAG = "settings";
#define APP_NS "app"

static const char *K_TZ_HOUR = "tz_hour";
static const char *K_UDP_HOST = "udp_host";
static const char *K_UDP_PORT = "udp_port";
static const char *K_UDP_TOKEN = "udp_token";

esp_err_t nvs_settings_init(void) {
    nvs_handle_t h;
    esp_err_t e = nvs_open(APP_NS, NVS_READWRITE, &h);
    if (e == ESP_OK) nvs_close(h);
    return e;
}

esp_err_t nvs_settings_get_tz_hour(int8_t *hour) {
    nvs_handle_t h;
    esp_err_t e = nvs_open(APP_NS, NVS_READONLY, &h);
    if (e != ESP_OK) { if (hour) *hour = 8; return ESP_OK; }
    int8_t v = 8;
    e = nvs_get_i8(h, K_TZ_HOUR, &v);
    nvs_close(h);
    if (e == ESP_ERR_NVS_NOT_FOUND) v = 8;
    if (hour) *hour = (v >= -12 && v <= 12) ? v : 8;
    return ESP_OK;
}

esp_err_t nvs_settings_set_tz_hour(int8_t hour) {
    if (hour < -12 || hour > 12) return ESP_ERR_INVALID_ARG;
    nvs_handle_t h;
    esp_err_t e = nvs_open(APP_NS, NVS_READWRITE, &h);
    if (e != ESP_OK) return e;
    e = nvs_set_i8(h, K_TZ_HOUR, hour);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    ESP_LOGI(TAG, "tz_hour = %d", (int)hour);
    return e;
}

// ---- 跨网直连配置(字符串读写统一走这里, cap 含结尾 NUL) ----

static esp_err_t get_str(nvs_handle_t h, const char *key,
                         char *buf, size_t cap, const char *dflt) {
    size_t len = 0;
    esp_err_t e = nvs_get_str(h, key, buf, &len);
    if (e == ESP_OK && len > cap) e = ESP_ERR_NVS_INVALID_LENGTH;
    if (e != ESP_OK) {
        strncpy(buf, dflt, cap - 1);
        buf[cap - 1] = '\0';
        return ESP_OK;   // 未配置=合法缺省, 不当错误
    }
    buf[cap - 1] = '\0';   // 防御: NVS 值理论带 NUL, 再兜一道
    return ESP_OK;
}

esp_err_t nvs_settings_get_udp_host(char *buf, size_t cap) {
    if (!buf || cap == 0) return ESP_ERR_INVALID_ARG;
    nvs_handle_t h;
    esp_err_t e = nvs_open(APP_NS, NVS_READONLY, &h);
    if (e != ESP_OK) { buf[0] = '\0'; return ESP_OK; }
    e = get_str(h, K_UDP_HOST, buf, cap, "");
    nvs_close(h);
    return e;
}

esp_err_t nvs_settings_set_udp_host(const char *host) {
    if (!host || strlen(host) >= NVS_UDP_HOST_MAX) return ESP_ERR_INVALID_ARG;
    nvs_handle_t h;
    esp_err_t e = nvs_open(APP_NS, NVS_READWRITE, &h);
    if (e != ESP_OK) return e;
    e = nvs_set_str(h, K_UDP_HOST, host);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    ESP_LOGI(TAG, "udp_host = '%s'", host);
    return e;
}

esp_err_t nvs_settings_get_udp_port(uint16_t *port) {
    if (!port) return ESP_ERR_INVALID_ARG;
    nvs_handle_t h;
    esp_err_t e = nvs_open(APP_NS, NVS_READONLY, &h);
    if (e != ESP_OK) { *port = 33333; return ESP_OK; }
    uint16_t v = 33333;
    e = nvs_get_u16(h, K_UDP_PORT, &v);
    nvs_close(h);
    if (e != ESP_OK) v = 33333;
    *port = v ? v : 33333;
    return ESP_OK;
}

esp_err_t nvs_settings_set_udp_port(uint16_t port) {
    if (port == 0) return ESP_ERR_INVALID_ARG;
    nvs_handle_t h;
    esp_err_t e = nvs_open(APP_NS, NVS_READWRITE, &h);
    if (e != ESP_OK) return e;
    e = nvs_set_u16(h, K_UDP_PORT, port);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    ESP_LOGI(TAG, "udp_port = %u", (unsigned)port);
    return e;
}

esp_err_t nvs_settings_get_udp_token(char *buf, size_t cap) {
    if (!buf || cap == 0) return ESP_ERR_INVALID_ARG;
    nvs_handle_t h;
    esp_err_t e = nvs_open(APP_NS, NVS_READONLY, &h);
    if (e != ESP_OK) { buf[0] = '\0'; return ESP_OK; }
    e = get_str(h, K_UDP_TOKEN, buf, cap, "");
    nvs_close(h);
    return e;
}

esp_err_t nvs_settings_set_udp_token(const char *token) {
    if (!token || strlen(token) >= NVS_UDP_TOKEN_MAX) return ESP_ERR_INVALID_ARG;
    nvs_handle_t h;
    esp_err_t e = nvs_open(APP_NS, NVS_READWRITE, &h);
    if (e != ESP_OK) return e;
    e = nvs_set_str(h, K_UDP_TOKEN, token);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    ESP_LOGI(TAG, "udp_token 已更新(len=%u)", (unsigned)strlen(token));
    return e;
}

void nvs_settings_factory_reset(void) {
    nvs_handle_t h;
    if (nvs_open(APP_NS, NVS_READWRITE, &h) == ESP_OK) {
        nvs_erase_all(h);
        nvs_commit(h);
        nvs_close(h);
        ESP_LOGW(TAG, "app 命名空间已清空");
    }
}
