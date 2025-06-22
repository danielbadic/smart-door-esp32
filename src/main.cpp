#include "esp_camera.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <esp_http_server.h>
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// Define LED, button, buzzer, and relay pins
#define FLASH_LED_PIN 4
#define BUTTON_PIN 13
#define BUZZER_PIN 14
#define RELAY_PIN 12

#define CAMERA_MODEL_AI_THINKER
#include "camera_pins.h"

// WiFi credentials
const char *ssid = "DEMOLATORII";
const char *password = "wintertime";

// Timing and state management
unsigned long relayStartTime = 0;
bool relayActive = false;
unsigned long lastButtonPress = 0;
const unsigned long BUTTON_DEBOUNCE = 2000;

// Performance settings
const unsigned long STREAM_DELAY_MS = 50; // ~20-30 FPS max
const unsigned long RELAY_DURATION = 3000;

// Server endpoint
const char *serverUrl = "http://192.168.0.103:5000/upload";
httpd_handle_t camera_httpd = NULL;

// ** Centralized camera buffer flushing**
void flushCameraBuffer()
{
    for (int i = 0; i < 2; i++)
    {
        camera_fb_t *flush_fb = esp_camera_fb_get();
        if (flush_fb)
        {
            esp_camera_fb_return(flush_fb);
        }
    }
}

// **Centralized CORS header setup**
void setCORSHeaders(httpd_req_t *req)
{
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Headers", "Content-Type");
}

// **Improved stream handler with frame rate control**
static esp_err_t stream_handler(httpd_req_t *req)
{
    camera_fb_t *fb = NULL;
    esp_err_t res = ESP_OK;
    size_t _jpg_buf_len = 0;
    uint8_t *_jpg_buf = NULL;
    char part_buf[128];

    setCORSHeaders(req);

    res = httpd_resp_set_type(req, "multipart/x-mixed-replace; boundary=frame");
    if (res != ESP_OK)
    {
        return res;
    }

    while (true)
    {
        unsigned long frameStart = millis();

        fb = esp_camera_fb_get();
        if (!fb)
        {
            Serial.println("Camera capture failed");
            res = ESP_FAIL;
            break; // Exit on persistent failures
        }

        _jpg_buf = NULL;
        _jpg_buf_len = 0;

        if (fb->format != PIXFORMAT_JPEG)
        {
            bool jpeg_converted = frame2jpg(fb, 80, &_jpg_buf, &_jpg_buf_len);
            if (!jpeg_converted)
            {
                Serial.println("JPEG compression failed");
                esp_camera_fb_return(fb);
                res = ESP_FAIL;
                break;
            }
        }
        else
        {
            _jpg_buf_len = fb->len;
            _jpg_buf = fb->buf;
        }

        // Send frame header
        size_t hlen = snprintf(part_buf, sizeof(part_buf),
                               "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n",
                               _jpg_buf_len);

        if ((res = httpd_resp_send_chunk(req, part_buf, hlen)) != ESP_OK ||
            (res = httpd_resp_send_chunk(req, (const char *)_jpg_buf, _jpg_buf_len)) != ESP_OK ||
            (res = httpd_resp_send_chunk(req, "\r\n", 2)) != ESP_OK)
        {
            break;
        }

        // Cleanup
        if (fb->format != PIXFORMAT_JPEG && _jpg_buf)
        {
            free(_jpg_buf);
        }
        esp_camera_fb_return(fb);
        fb = NULL;

        // **PERFORMANCE FIX: Frame rate limiting**
        unsigned long frameTime = millis() - frameStart;
        if (frameTime < STREAM_DELAY_MS)
        {
            delay(STREAM_DELAY_MS - frameTime);
        }
    }

    // Cleanup on exit
    if (fb)
    {
        esp_camera_fb_return(fb);
    }
    if (_jpg_buf && fb && fb->format != PIXFORMAT_JPEG)
    {
        free(_jpg_buf);
    }

    return res;
}

// **control handler**
static esp_err_t control_handler(httpd_req_t *req)
{
    char query[100];

    setCORSHeaders(req);

    if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK)
    {
        if (strstr(query, "action=open") != NULL)
        {
            Serial.println("Door open command received");

            // **Set relay state immediately, let loop() handle timing**
            digitalWrite(BUZZER_PIN, HIGH);
            digitalWrite(RELAY_PIN, HIGH);
            delay(1000);
            digitalWrite(FLASH_LED_PIN, HIGH);

            relayStartTime = millis();
            relayActive = true;

            // ** Immediate response, no delay**
            return httpd_resp_send(req, "{\"status\":\"success\",\"message\":\"Door opened\"}", -1);
        }
    }

    return httpd_resp_send(req, "{\"status\":\"error\",\"message\":\"Invalid action\"}", -1);
}

// **capture handler**
static esp_err_t capture_handler(httpd_req_t *req)
{
    setCORSHeaders(req);

    // Use centralized buffer flushing
    flushCameraBuffer();

    camera_fb_t *fb = esp_camera_fb_get();
    if (!fb)
    {
        Serial.println("Camera capture failed");
        return httpd_resp_send_500(req);
    }

    httpd_resp_set_type(req, "image/jpeg");
    httpd_resp_set_hdr(req, "Content-Disposition", "inline; filename=capture.jpg");

    esp_err_t res = httpd_resp_send(req, (const char *)fb->buf, fb->len);
    esp_camera_fb_return(fb);

    return res;
}

static esp_err_t options_handler(httpd_req_t *req)
{
    setCORSHeaders(req);
    return httpd_resp_send(req, "", 0);
}

void startCameraServer()
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;

    httpd_uri_t stream_uri = {.uri = "/stream", .method = HTTP_GET, .handler = stream_handler, .user_ctx = NULL};
    httpd_uri_t control_uri = {.uri = "/control", .method = HTTP_GET, .handler = control_handler, .user_ctx = NULL};
    httpd_uri_t capture_uri = {.uri = "/capture", .method = HTTP_GET, .handler = capture_handler, .user_ctx = NULL};
    httpd_uri_t options_uri = {.uri = "/*", .method = HTTP_OPTIONS, .handler = options_handler, .user_ctx = NULL};

    Serial.printf("Starting web server on port: '%d'\n", config.server_port);
    if (httpd_start(&camera_httpd, &config) == ESP_OK)
    {
        httpd_register_uri_handler(camera_httpd, &stream_uri);
        httpd_register_uri_handler(camera_httpd, &control_uri);
        httpd_register_uri_handler(camera_httpd, &capture_uri);
        httpd_register_uri_handler(camera_httpd, &options_uri);
    }
}

void setup()
{
    Serial.begin(115200);
    WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);

    // Pin setup
    pinMode(FLASH_LED_PIN, OUTPUT);
    pinMode(BUTTON_PIN, INPUT_PULLUP);
    pinMode(BUZZER_PIN, OUTPUT);
    pinMode(RELAY_PIN, OUTPUT);

    digitalWrite(FLASH_LED_PIN, LOW);
    digitalWrite(BUZZER_PIN, LOW);
    digitalWrite(RELAY_PIN, LOW);

    // **WiFi connection with timeout**
    WiFi.begin(ssid, password);
    Serial.print("Connecting to WiFi");
    unsigned long wifiStart = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < 15000)
    {
        delay(500);
        Serial.print(".");
    }

    if (WiFi.status() == WL_CONNECTED)
    {
        Serial.println(" connected!");
        Serial.print("Camera IP: ");
        Serial.println(WiFi.localIP());
    }
    else
    {
        Serial.println(" connection failed!");
        return;
    }

    // **Camera configuration**
    camera_config_t config;
    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    config.xclk_freq_hz = 20000000;
    config.pixel_format = PIXFORMAT_JPEG;

    if (psramFound())
    {
        config.frame_size = FRAMESIZE_VGA;
        config.jpeg_quality = 15;
        config.fb_count = 1;
    }
    else
    {
        config.frame_size = FRAMESIZE_QVGA;
        config.jpeg_quality = 20;
        config.fb_count = 1;
    }

    if (esp_camera_init(&config) != ESP_OK)
    {
        Serial.println("Camera init failed");
        return;
    }

    startCameraServer();

    Serial.println("Setup complete!");
    Serial.printf("Stream: http://%s/stream\n", WiFi.localIP().toString().c_str());
    Serial.printf("Control: http://%s/control?action=open\n", WiFi.localIP().toString().c_str());
}

void loop()
{
    unsigned long currentTime = millis();

    // **relay control**
    if (relayActive && (currentTime - relayStartTime >= RELAY_DURATION))
    {
        digitalWrite(BUZZER_PIN, LOW);
        digitalWrite(RELAY_PIN, LOW);
        digitalWrite(FLASH_LED_PIN, LOW);
        relayActive = false;
        Serial.println("Relay deactivated");
    }
}