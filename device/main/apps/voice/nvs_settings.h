// main/apps/voice/nvs_settings.h —— 持久化设置(NVS 命名空间 "app")。
#pragma once

#include "esp_err.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

esp_err_t nvs_settings_init(void);

// 时区偏移小时(int8,±12;缺省 8)
esp_err_t nvs_settings_get_tz_hour(int8_t *hour);
esp_err_t nvs_settings_set_tz_hour(int8_t hour);

// ---- 跨网直连配置(2026-10-14,v2 协议, 由 PC 经 cfg.udp 下行写入) ----
// udp_host: 静态服务器地址(IP 或域名, ≤63B;空串=未配置, 只走局域网 beacon)
// udp_port: 缺省 33333(隧道远端端口, SunnyNgrok/frp 分配的可能不是 33333)
// udp_token: 配对令牌(≤31B;非空时上行包带 4B FNV-1a 校验尾, beacon 需带 t 字段)
#define NVS_UDP_HOST_MAX 64
#define NVS_UDP_TOKEN_MAX 32
esp_err_t nvs_settings_get_udp_host(char *buf, size_t cap);
esp_err_t nvs_settings_set_udp_host(const char *host);
esp_err_t nvs_settings_get_udp_port(uint16_t *port);
esp_err_t nvs_settings_set_udp_port(uint16_t port);
esp_err_t nvs_settings_get_udp_token(char *buf, size_t cap);
esp_err_t nvs_settings_set_udp_token(const char *token);

void nvs_settings_factory_reset(void);   // 清 "app" 命名空间

#ifdef __cplusplus
}
#endif
