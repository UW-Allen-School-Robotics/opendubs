#include <Arduino.h>
#include "esp_idf_version.h"
#include "esp_cam_ctlr.h"          // esp_driver_cam — the MIPI-CSI camera controller
#include "esp_cam_ctlr_csi.h"
#include "driver/isp.h"            // image signal processor the CSI path feeds

void setup() {
    Serial.begin(115200);
    delay(500);
    Serial.printf("dual-framework OK — IDF %d.%d.%d\n",
                  ESP_IDF_VERSION_MAJOR, ESP_IDF_VERSION_MINOR, ESP_IDF_VERSION_PATCH);
    // Reference the types so the linker has to actually resolve the component.
    esp_cam_ctlr_csi_config_t cfg = {};
    cfg.h_res = 1600; cfg.v_res = 1200;      // SC2356 native
    esp_cam_ctlr_handle_t h = nullptr;
    Serial.printf("csi cfg %ux%u  handle=%p\n", cfg.h_res, cfg.v_res, h);
}
void loop() { delay(1000); }
