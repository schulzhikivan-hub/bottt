# VPS как рабочее место для одного личного TikTok-аккаунта

Удалённый рабочий стол на Ubuntu 22.04: XFCE + xrdp, Google Chrome с постоянным профилем.
Вы заходите в аккаунт сами, руками. Автопостинга, эмуляторов и скриптов, имитирующих действия, здесь нет.

Схема:

```
телефон ── Tailscale ──► VPS ──► xrdp (RDP) ──► XFCE ──► Chrome (профиль ~/tiktok-profile)
   │
   └── GitHub app ──► репозиторий ──► push в main ──► self-hosted runner на VPS ──► setup/install.sh
```

Конфиги меняются через git: правите файл, пушите в `main`, runner применяет изменения на VPS.

## Что где лежит

| Путь | Что делает |
|---|---|
| `setup/install.sh` | Точка входа: запускает шаги по порядку, безопасен при повторном запуске |
| `setup/01-base.sh` | Обновление индексов пакетов, часовой пояс, пользователь владельца |
| `setup/02-desktop.sh` | XFCE и xrdp |
| `setup/03-chrome.sh` | Google Chrome и ярлык с профилем TikTok |
| `setup/04-security.sh` | SSH только по ключам, ufw, fail2ban |
| `setup/05-check-ip.sh` | Записывает текущий IP и локацию в `~/ip-log.txt` |
| `setup/backup-profile.sh` | Зашифрованный бэкап профиля Chrome |
| `setup/settings.env` | Имя пользователя и часовой пояс |
| `configs/` | Файлы, которые копируются в систему: xrdp, ufw, fail2ban, sshd, Chrome |
| `.github/workflows/deploy.yml` | Применяет setup на VPS при push в `main` или вручную |

## Настройка один раз

Часть шагов требует шелла на VPS. Это можно сделать с телефона через SSH-клиент (например, Termius, с SSH-ключом).

### 1. Репозиторий

Сделайте репозиторий **приватным**. Self-hosted runner выполняет код из репозитория на вашем VPS, и открытый репозиторий позволил бы любому запускать код на сервере.

### 2. Пользователь владельца и SSH-ключ

На VPS под root:

```bash
adduser --disabled-password --gecos "" owner
mkdir -p /home/owner/.ssh
echo "ВАШ_ПУБЛИЧНЫЙ_КЛЮЧ" > /home/owner/.ssh/authorized_keys
chown -R owner:owner /home/owner/.ssh
chmod 700 /home/owner/.ssh && chmod 600 /home/owner/.ssh/authorized_keys
sudo passwd owner   # пароль нужен для входа по RDP
```

Ключ обязателен до первого запуска деплоя: иначе скрипт откажется отключать вход по паролю, чтобы не закрыть доступ.

### 3. Клонирование и первый запуск

```bash
git clone https://github.com/OWNER/REPO.git /root/bottt
cd /root/bottt && sudo ./setup/install.sh
```

Скрипт идемпотентен, поэтому дальше его можно не запускать вручную: это сделает workflow.

### 4. Self-hosted runner

В GitHub: **Settings → Actions → Runners → New self-hosted runner → Linux x64**. Создайте пользователя без прав администратора и поставьте runner по команде из GitHub, с меткой `vps`:

```bash
adduser --disabled-password --gecos "" gh-runner
su - gh-runner
# команды download / config из страницы GitHub, при config добавьте:
# --labels vps --unattended
exit
cd /home/gh-runner/actions-runner
sudo ./svc.sh install gh-runner
sudo ./svc.sh start
```

Пути в sudoers ниже зависят от имени репозитория. Если репозиторий называется не `bottt`, поправьте путь.

Разрешите runner запускать только установку, без пароля и без лишних прав:

```bash
sudo visudo -f /etc/sudoers.d/gh-runner
```

Строка в файле:

```
gh-runner ALL=(root) NOPASSWD: /home/gh-runner/actions-runner/_work/bottt/bottt/setup/install.sh
```

Важно: этот разрешённый скрипт меняет систему от root. Доступ на запись в репозиторий и к `main` должен быть только у вас.

### 5. Tailscale (доступ с телефона)

RDP открыт только в сети Tailscale, извне порт 3389 недоступен. Установите Tailscale на VPS:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up   # откроется ссылка, войдите со своего телефона
tailscale ip -4     # запишите адрес вида 100.x.y.z
```

Установите приложение Tailscale на телефон и войдите тем же аккаунтом.

### 6. Первый деплой

Запускайте после шагов 2–5. В GitHub app: **Actions → Deploy VPS config → Run workflow**. Или сделайте любой push в `main`.

## Как работать с телефона

1. Откройте Tailscale и убедитесь, что VPS в сети.
2. Откройте **Microsoft Remote Desktop** (iOS/Android):
   - Адрес ПК: `100.x.y.z` (из `tailscale ip -4`)
   - Пользователь: `owner`, пароль из шага 2
3. В XFCE откройте меню и запустите **TikTok (Chrome)**. Chrome откроет `tiktok.com` с профилем `~/tiktok-profile`.
4. Входите в аккаунт сами. Код подтверждения вводите вручную.

Перезапуск конфигов с телефона: GitHub app → Actions → Deploy VPS config → Run workflow.

## Проверка IP

Прокси не настраиваем. Вход идёт через IP VPS, поэтому выбирайте VPS в стране, где вы реально живёте. Проверка стабильности:

```bash
./setup/05-check-ip.sh   # от owner, пишет ~/ip-log.txt
```

Если IP меняется без вашего участия, обратитесь к хостеру. Не маскируйте IP дата-центра под домашний: это обход проверок платформы.

## Бэкап и восстановление профиля

Бэкап (от owner, Chrome закроется сам, скрипт спросит пароль для шифрования):

```bash
./setup/backup-profile.sh
```

Архив лежит в `~/backups/`, он содержит ваши сессии. Храните его вне VPS и не выкладывайте никуда.

Восстановление на том же VPS:

```bash
gpg -d ~/backups/ИМЯ.tar.gz.gpg | tar xzf - -C "$HOME"
```

Профиль нельзя переносить на другую машину: Chrome шифрует cookies ключом, привязанным к системе.

## Дисциплина

- Один VPS, один браузер, один профиль (`~/tiktok-profile`). Второй профиль Chrome сюда не добавляйте.
- Не входите в аккаунт параллельно с телефона и другого компьютера.
- Стабильный IP: VPS в одной стране, без смены региона и провайдера.
- 2FA обязательна: Настройки → Безопасность и вход → Двухэтапная аутентификация, лучше через приложение-аутентификатор.
- Если пришло письмо о входе с нового устройства и это были не вы: смените пароль и выйдите из всех сессий.

## Чеклист первого входа

**До первого входа**
- [ ] Репозиторий приватный
- [ ] `owner` создан, SSH-ключ в `/home/owner/.ssh/authorized_keys`, задан пароль для RDP
- [ ] runner `vps` online в настройках GitHub, sudoers настроен
- [ ] Tailscale на VPS и телефоне, виден `100.x.y.z`
- [ ] Деплой прошёл зелёным
- [ ] `ufw status` показывает разрешения OpenSSH и 3389 только на `tailscale0`

**Первый вход**
- [ ] RDP с телефона подключился, открылся XFCE
- [ ] Запущен ярлык TikTok (Chrome)
- [ ] Вход выполнен вручную, код введён вами
- [ ] Включена 2FA
- [ ] В настройках безопасности аккаунта нет чужих сессий

**После перезагрузки**
- [ ] `sudo reboot`, переподключились через Tailscale
- [ ] TikTok открывается без повторного входа
- [ ] Сделан первый бэкап, архив распаковывается

**Регулярно**
- [ ] Раз в неделю: `sudo apt upgrade` на VPS, проверка `~/ip-log.txt`
- [ ] Бэкап профиля после важных изменений, хранится вне VPS
