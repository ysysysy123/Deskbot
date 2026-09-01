#include "Eye_UART.h"

#include <Arduino.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

#include "LVGL_Example.h"

namespace {

constexpr uint32_t EYE_UART_BAUD = 115200;
constexpr int EYE_UART_RX_PIN = 44;
constexpr int EYE_UART_TX_PIN = 43;
constexpr size_t LINE_CAPACITY = 64;

HardwareSerial eye_uart(1);
char line_buffer[LINE_CAPACITY];
size_t line_length = 0;
bool line_overflow = false;

void reply(const char *message)
{
  eye_uart.print(message);
  eye_uart.print('\n');
}

bool parse_percent(const char *text, int8_t &value)
{
  if (text == NULL || *text == '\0') {
    return false;
  }
  char *end = NULL;
  const long parsed = strtol(text, &end, 10);
  if (*end != '\0' || parsed < -100 || parsed > 100) {
    return false;
  }
  value = static_cast<int8_t>(parsed);
  return true;
}

bool parse_state(const char *name, EyeState &state)
{
  if (name == NULL) {
    return false;
  }
  if (strcmp(name, "IDLE") == 0) {
    state = EyeState::IDLE;
  } else if (strcmp(name, "LISTENING") == 0) {
    state = EyeState::LISTENING;
  } else if (strcmp(name, "THINKING") == 0) {
    state = EyeState::THINKING;
  } else if (strcmp(name, "SPEAKING") == 0) {
    state = EyeState::SPEAKING;
  } else if (strcmp(name, "HAPPY") == 0) {
    state = EyeState::HAPPY;
  } else if (strcmp(name, "SAD") == 0) {
    state = EyeState::SAD;
  } else if (strcmp(name, "ANGRY") == 0) {
    state = EyeState::ANGRY;
  } else if (strcmp(name, "SURPRISED") == 0) {
    state = EyeState::SURPRISED;
  } else if (strcmp(name, "SLEEPING") == 0) {
    state = EyeState::SLEEPING;
  } else {
    return false;
  }
  return true;
}

void uppercase_ascii(char *text)
{
  for (; *text != '\0'; ++text) {
    *text = static_cast<char>(toupper(static_cast<unsigned char>(*text)));
  }
}

void process_line(char *line)
{
  while (*line == ' ' || *line == '\t') {
    ++line;
  }
  if (*line == '\0') {
    return;
  }

  uppercase_ascii(line);
  char *save = NULL;
  char *command = strtok_r(line, " \t", &save);

  if (strcmp(command, "PING") == 0 && strtok_r(NULL, " \t", &save) == NULL) {
    reply("PONG 1");
    return;
  }
  if (strcmp(command, "BLINK") == 0 && strtok_r(NULL, " \t", &save) == NULL) {
    reply(Eye_RequestBlink() ? "OK BLINK" : "ERR BUSY");
    return;
  }
  if (strcmp(command, "STATE") == 0) {
    char *name = strtok_r(NULL, " \t", &save);
    EyeState state;
    if (name == NULL || strtok_r(NULL, " \t", &save) != NULL ||
        !parse_state(name, state)) {
      reply("ERR STATE");
      return;
    }
    if (!Eye_RequestState(state)) {
      reply("ERR BUSY");
      return;
    }
    eye_uart.print("OK STATE ");
    eye_uart.println(name);
    return;
  }
  if (strcmp(command, "GAZE") == 0) {
    char *x_text = strtok_r(NULL, " \t", &save);
    char *y_text = strtok_r(NULL, " \t", &save);
    int8_t x = 0;
    int8_t y = 0;
    if (strtok_r(NULL, " \t", &save) != NULL ||
        !parse_percent(x_text, x) || !parse_percent(y_text, y)) {
      reply("ERR GAZE");
      return;
    }
    reply(Eye_RequestGaze(x, y) ? "OK GAZE" : "ERR BUSY");
    return;
  }
  if (strcmp(command, "HELP") == 0 && strtok_r(NULL, " \t", &save) == NULL) {
    reply("CMDS PING STATE GAZE BLINK");
    return;
  }
  reply("ERR COMMAND");
}

}  // namespace

void EyeUart_Init(void)
{
  eye_uart.setRxBufferSize(256);
  eye_uart.begin(EYE_UART_BAUD, SERIAL_8N1, EYE_UART_RX_PIN, EYE_UART_TX_PIN);
  reply("READY EYE_UART_V1");
}

void EyeUart_Update(void)
{
  while (eye_uart.available() > 0) {
    const int incoming = eye_uart.read();
    if (incoming == '\r') {
      continue;
    }
    if (incoming == '\n') {
      if (line_overflow) {
        reply("ERR OVERFLOW");
      } else {
        line_buffer[line_length] = '\0';
        process_line(line_buffer);
      }
      line_length = 0;
      line_overflow = false;
      continue;
    }
    if (line_length + 1 < LINE_CAPACITY) {
      line_buffer[line_length++] = static_cast<char>(incoming);
    } else {
      line_overflow = true;
    }
  }
}
