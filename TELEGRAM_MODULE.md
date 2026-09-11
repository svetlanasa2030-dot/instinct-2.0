# Telegram OCR module

This module is independent from the existing screen-point automation.

Required behavior:
- monitor a selected game-chat region;
- use RapidOCR locally;
- use OCR.Space as fallback when local OCR is unavailable;
- process only messages beginning with "Лично";
- combine OCR lines when a personal message is split across lines;
- ignore incomplete "Лично" entries without player and message;
- send exactly two fields:
  Игрок: ...
  Сообщение: ...
- never send the old Status/User/Message format;
- keep every nickname character, including Unicode and special symbols;
- prevent duplicate messages;
- use Bot Token + Chat ID;
- retry Telegram delivery up to 3 times with a short delay;
- report success as "ОТПРАВЛЕНО" and final failure as "НЕ ОТПРАВЛЕНО после 3 попыток".
