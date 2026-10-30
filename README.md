# VPS как рабочее место для одного личного TikTok-аккаунта

Удалённый рабочий стол на Ubuntu 22.04: XFCE + xrdp, Google Chrome с постоянным профилем
и опциональным прокси только для этого Chrome.
Вы заходите в аккаунт сами, руками. Автопостинга, эмуляторов и скриптов, имитирующих действия, здесь нет.

Требования к VPS: Ubuntu 22.04, минимум 2 ГБ RAM (рекомендуется 4 ГБ). На 1 ГБ XFCE + Chrome будут сильно тормозить.

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
| `setup/05-check-ip.sh` | Записывает IP VPS и IP через прокси в `~/ip-log.txt` |
| `setup/backup-profile.sh` | Зашифрованный бэкап профиля Chrome |
| `setup/settings.env` | Имя пользователя, часовой пояс, прокси для Chrome |
| `configs/` | Файлы, которые копируются в систему: xrdp, ufw, fail2ban, sshd, Chrome |
| `.github/workflows/deploy.yml` | Применяет setup на VPS при push в `main` или вручную |

## Автоустановка (рекомендуется, с телефона)

Один запуск из GitHub ставит всё: пользователя owner с ключом и паролем, Tailscale, runner, sudoers и полную установку.

### Шаг A. Добавить секреты

Секреты — это зашифрованные значения, их не видно после сохранения. Откройте страницу и нажимайте **New repository secret**:

**https://github.com/schulzhikivan-hub/bottt/settings/secrets/actions**

| Имя секрета | Что записать | Где взять |
|---|---|---|
| `VPS_HOST` | IP-адрес VPS | Панель хостера, раздел с серверами |
| `VPS_ROOT_PASSWORD` | Root-пароль VPS | Письмо или панель хостера (у многих можно сбросить пароль) |
| `OWNER_PUBKEY` | Публичный SSH-ключ | Termius → Keychain → Generate → Copy public key |
| `OWNER_PASSWORD` | Пароль для входа по RDP | Придумайте длинный, сохраните в менеджере паролей |
| `TS_AUTHKEY` | Ключ Tailscale | **https://login.tailscale.com/admin/settings/keys** → Generate auth key. Reusable: выключено, Ephemeral: выключено |
| `RUNNER_ADMIN_TOKEN` | Токен GitHub с правом на runner | См. ниже |

**Как получить `RUNNER_ADMIN_TOKEN`:** откройте **https://github.com/settings/personal-access-tokens/new**:
- Token name: `vps-bootstrap`
- Expiration: 7 дней
- Repository access: **Only select repositories** → `bottt`
- Repository permissions: **Administration: Read and write**, Metadata: Read-only
- Generate token. Скопируйте значение сразу, оно показывается один раз.

После установки токен можно удалить: https://github.com/settings/tokens

**Важно про root:** установка заходит на VPS по root-паролю. Если у хостера вход root по паролю отключён, workflow упадёт с `Permission denied`. Тогда скажите, я переделаю вход на ключ.

### Шаг B. Запустить

**https://github.com/schulzhikivan-hub/bottt/actions/workflows/bootstrap.yml** → **Run workflow** → выбрать ветку `claude/telegram-bot-system-kssc6g` → **Run workflow**.

Дождитесь зелёной галочки (обычно 5–15 минут). Если красная, откройте шаг с ошибкой и пришлите текст ошибки.

Когда зелёный:
- runner появится в **https://github.com/schulzhikivan-hub/bottt/settings/actions/runners**,
- Tailscale на VPS подключён, его адрес — в admin-консоли Tailscale,
- можно подключаться через Microsoft Remote Desktop, как описано ниже.

После этого секреты `RUNNER_ADMIN_TOKEN` и `VPS_ROOT_PASSWORD` можно удалить со страницы секретов.

## Настройка один раз вручную

Альтернатива автоустановке: те же шаги руками через Termius. Часть шагов требует шелла на VPS. Это можно сделать с телефона через SSH-клиент (например, Termius, с SSH-ключом).

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

## Прокси только для Chrome

Без прокси Chrome выходит в интернет с IP самого VPS. Это IP дата-центра, и TikTok чаще просит подтверждение входа с таких адресов. Прокси меняет IP только для Chrome с профилем TikTok. Система, `apt`, runner и SSH идут напрямую.

### Как включить (с телефона через GitHub)

1. Откройте в GitHub app файл `setup/settings.env` и отредактируйте строку:
   ```
   CHROME_PROXY=http://proxy.example.com:8080
   ```
   или `socks5://host:port`.
2. Закоммитьте в `main`. Workflow применит настройку на VPS.
3. Закройте Chrome на VPS и запустите ярлык TikTok (Chrome) заново.

Выключить прокси: оставьте `CHROME_PROXY=` пустым и закоммитьте.

### Логин и пароль прокси

Не пишите их в `settings.env`: файл лежит в репозитории, и скрипт откажется применять адрес с логином.

- **Лучший вариант:** авторизация по IP. В кабинете провайдера прокси добавьте IP вашего VPS в белый список. Тогда логин не нужен вообще.
- **HTTP-прокси с логином:** Chrome при первом запросе сам покажет окно ввода логина и пароля. Вводите вручную.
- **SOCKS5 с логином** Chrome не поддерживает. Для SOCKS5 подходит только авторизация по IP.

### Какой прокси брать

- **Статический** (один и тот же IP), а не ротируемый. Ротация, при которой IP меняется каждые несколько минут, для входа в аккаунт хуже, чем IP дата-центра.
- **Страна прокси = ваша страна и `TIMEZONE` в `settings.env`.**
- Один прокси на этот аккаунт, не меняйте его без нужды.

### Проверка IP

От owner:

```bash
./setup/05-check-ip.sh   # пишет в ~/ip-log.txt IP VPS и IP через прокси
```

В самом Chrome откройте `https://ipinfo.io`: там должен быть IP прокси, а не VPS. Если IP через прокси меняется между проверками, прокси ротируемый или нестабильный.

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
- Стабильный IP: один статический прокси (или IP VPS без прокси), без смены страны и провайдера.
- Часовой пояс VPS и язык Chrome соответствуют стране этого IP.
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
- [ ] Если используете прокси: `CHROME_PROXY` задан, IP VPS в белом списке провайдера, `05-check-ip.sh` показывает IP прокси в вашей стране

**Первый вход**
- [ ] RDP с телефона подключился, открылся XFCE
- [ ] Запущен ярлык TikTok (Chrome)
- [ ] В Chrome `https://ipinfo.io` показывает ожидаемый IP (прокси или VPS)
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
