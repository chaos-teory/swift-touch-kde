# swift-touch-kde

Интеграция мультимедийного тачпада Acer Swift для KDE Plasma на Linux.

English version: [README.md](README.md)

Проект включает:
- декодирование событий мультимедийной полосы на поддерживаемых тачпадах Acer Swift;
- генерацию медиа-клавиш (`Play/Pause`, `Previous`, `Next`);
- tray-приложение и GUI-настройки для KDE;
- интеграцию в `Параметры системы` KDE;
- workflow сборки и установки Debian-пакета (`.deb`).

## Статус

Текущая основная поддержка:
- `Acer Swift SFG16-74`
- HID тачпада: `PIXA480A:00 093A:480A`

Проект рабочий, но пока экспериментальный. На других моделях поведение не гарантируется.

## Установка (Рекомендуется)

Установка из GitHub Releases:

1. Откройте последний релиз:
   - `https://github.com/chaos-teory/swift-touch-kde/releases/latest`
2. Скачайте:
   - `swift-touch-kde_<version>_all.deb`
   - `swift-touch-kde_<version>_all.deb.sha256`
3. Установите пакет:

```bash
sudo apt install ./swift-touch-kde_*_all.deb
```

4. Выполните пользовательскую настройку (под обычным пользователем, не root):

```bash
swift-touch-kde-user-setup
```

После установки:
- CLI-инструмент: `swift-touch-kde`
- GUI-настройки: `swift-touch-kde-settings`
- tray-приложение: `swift-touch-kde-tray`
- пользовательский сервис: `swift-touch-media.service`
- модуль KDE System Settings: `Swift Touch KDE`

## Сборка Debian-пакета

Локальная сборка:

```bash
sudo apt install -y debhelper dh-python python3-all
cd swift-touch-kde
dpkg-buildpackage -us -uc -b
```

Установка собранного пакета:

```bash
sudo apt install ../swift-touch-kde_*_all.deb
swift-touch-kde-user-setup
```

## Установка из исходников (для разработки)

Если нужно запускать напрямую из исходников, а не из `.deb`:

```bash
git clone https://github.com/chaos-teory/swift-touch-kde.git
mkdir -p ~/git
mv swift-touch-kde ~/git/
cd ~/git/swift-touch-kde
sudo ./install_kde_app.sh "$USER"
```

Примечание: `install_kde_app.sh` ожидает путь репозитория `~/git/swift-touch-kde` у целевого пользователя.

## Использование

Справка по всем командам:

```bash
swift-touch-kde --help
```

Частые команды:

```bash
# сканирование устройств
sudo swift-touch-kde scan

# запуск в daemon-режиме с печатью отладки
sudo swift-touch-kde run-media-keys --dev /dev/hidraw0 --emit print --seconds 30

# чтение регистра media-mode
sudo swift-touch-kde mtp-read --dev /dev/hidraw0 --reg 120

# просмотр consumer-репортов
sudo swift-touch-kde watch-consumer --seconds 30 --only-press
```

Управление сервисом:

```bash
systemctl --user status swift-touch-media.service
systemctl --user restart swift-touch-media.service
journalctl --user -u swift-touch-media.service -f
```

## Диагностика

- Если сервис не может получить доступ к `/dev/hidraw0` или `/dev/uinput`, выполните:

```bash
swift-touch-kde-user-setup
```

- Если тачпад уходит в некорректное аппаратное состояние, может потребоваться полный power cycle.

- Посмотреть текущие логи сервиса:

```bash
journalctl --user -u swift-touch-media.service -n 120 --no-pager
```

## Удаление

```bash
sudo apt purge swift-touch-kde
```

## Заметки по реверсу

Заметки по reverse engineering Windows-части:
- `docs/windows_reverse_acer5.md`
