# Развёртывание по ссылке и автозагрузка

Для владельца репозитория и сервера. GitHub Actions не хостит сайт: он
подключается к вашему серверу и обновляет на нём проект. Пароли, ключ GigaChat и
сертификат лежат **только на сервере**, в GitHub их нет.

## Что понадобится

- Сервер с Ubuntu 24.04, ориентир 2 ядра и 4 ГБ памяти, внешний IP. Для данных
  сотрудников — в России.
- Домен с записью A на IP сервера (записи AAAA без IPv6 быть не должно).
- Открытые порты 80 и 443, SSH только для вас и GitHub Actions.
- Права администратора в репозитории: секреты добавляет владелец.

## 1. Влить код

Pull request `audit-fixes` → `submain`, затем `submain` → `main`.
Разворачивается только `main`.

## 2. Подготовить сервер (под root)

```bash
apt update && apt install -y docker.io docker-compose-v2 git curl openssl
docker compose up --help | grep -- --wait-timeout   # строка должна найтись
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
install -d -o deploy -g deploy /opt/mgts
su - deploy -c 'git clone --branch main https://github.com/dolmi69/mgts-hackathon.git /opt/mgts'
```

Группа `docker` фактически даёт права root на этом сервере. Не держите на нём
другие важные проекты.

## 3. Настройки на сервере (под deploy)

```bash
su - deploy
cd /opt/mgts
install -m 600 .env.example .env
openssl rand -hex 24      # выполнить отдельно для каждого пароля и секрета
nano .env
```

```dotenv
DOMAIN=demo.example.ru
APP_ENV=demo
SEED_ON_STARTUP=true
DASHBOARD_USER=hr
DASHBOARD_PASSWORD=<пароль HR>
DASHBOARD_VIEWER_USER=jury
DASHBOARD_VIEWER_PASSWORD=<другой пароль, только просмотр>
WEBHOOK_SECRET=<случайная строка>
POSTGRES_PASSWORD=<случайная строка>
```

`POSTGRES_PASSWORD` меняйте только до первого запуска: база запоминает его.
`APP_ENV=demo` с вымышленными анкетами — для показа. Для настоящих данных нужна
отдельная пустая база, `APP_ENV=pilot` и `SEED_ON_STARTUP=false`.

## 4. GigaChat (если нужна живая модель)

Адреса GigaChat подписаны сертификатом Минцифры. Он нужен только серверу и
используется только в запросах приложения к GigaChat; в систему его не ставим.

```bash
cd /opt/mgts
curl -fsS -o backend/data/russian_trusted_root_ca_pem.crt \
  https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
openssl x509 -in backend/data/russian_trusted_root_ca_pem.crt -noout -fingerprint -sha256
```

Отпечаток должен быть таким (скрипт проверит его и сам):
`D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31`

```dotenv
LLM_PROVIDER=gigachat
LLM_API_KEY=<ключ авторизации>
GIGACHAT_MODEL=GigaChat-2
GIGACHAT_CA_BUNDLE=data/russian_trusted_root_ca_pem.crt
```

Не отключайте проверку сертификата. Без GigaChat оставьте `LLM_PROVIDER=mock`.

## 5. Первый запуск вручную

```bash
cd /opt/mgts && deploy/deploy.sh "$(git rev-parse HEAD)"
```

Откройте `https://ваш-домен`. Разбор 158 демо-анкет через GigaChat занимает около
пяти минут. Модель и пароли проверьте до включения автозагрузки.

## 6. Ключ для GitHub Actions

На своём компьютере создайте отдельный ключ без пароля:

```bash
ssh-keygen -t ed25519 -f mgts_deploy -N "" -C github-deploy
```

На сервере под `deploy` добавьте **одну строку** в `~/.ssh/authorized_keys`
(каталог с правами 700, файл 600), подставив содержимое `mgts_deploy.pub`:

```text
restrict,command="/opt/mgts/deploy/deploy.sh" ssh-ed25519 AAAA... github-deploy
```

Теперь ключом можно только развернуть коммит из `main`; оболочку он не открывает.
Кто может менять `main`, тот по сути управляет сервером: защитите ветку.

Отпечаток сервера для проверки подключения выполните **на самом сервере**:

```bash
awk -v h=IP_СЕРВЕРА '{print h, $1, $2}' /etc/ssh/ssh_host_ed25519_key.pub
```

Если SSH не на порту 22, вместо `IP` нужно `[IP]:ПОРТ`.

## 7. Настроить GitHub (владелец)

1. **Settings → Branches**: защитить `main` — только через pull request с зелёными
   тестами.
2. **Settings → Environments → New environment** `production`; *Deployment
   branches* — только `main`. Для подтверждения вручную добавьте *Required reviewers*.
3. В этом окружении — **secrets**:

| Секрет | Значение |
| --- | --- |
| `DEPLOY_HOST` | IP сервера, тот же, что в отпечатке |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | всё содержимое файла `mgts_deploy` |
| `DEPLOY_KNOWN_HOSTS` | строка из шага 6 |

4. Там же **variables**: `DEPLOY_URL` = `https://ваш-домен`; при другом порте SSH —
   `DEPLOY_PORT`.
5. **Settings → Secrets and variables → Actions → Variables** (уровень репозитория):
   `DEPLOY_ENABLED` = `true`. До этого автозагрузка выключена.
6. Удалить файл `mgts_deploy` со своего компьютера.
7. **Actions → Deploy → Run workflow** на ветке `main`.

Дальше каждый merge в `main` запускает тесты, затем обновление. Если новая версия не
поднялась, скрипт возвращает предыдущую. База при откате не возвращается.

## Если не работает

| Что видно | Что проверить |
| --- | --- |
| Deploy пропущен | `DEPLOY_ENABLED` на уровне репозитория, ветка `main` |
| `... is not configured` | секреты заданы в окружении `production` |
| `Host key verification failed` | адрес в `DEPLOY_HOST` и `DEPLOY_KNOWN_HOSTS` совпадает |
| `.env must be readable...` | `chmod 600 /opt/mgts/.env` |
| `unexpected GigaChat root certificate` | скачайте файл заново, шаг 4 |
| Нет HTTPS | запись A, порты 80/443, `docker compose logs caddy` |
| «Ошибка анализа» | `docker compose logs worker` |
| 429 при входе | 5 минут после 10 неверных паролей с одного адреса |

## После защиты

Отозвать ключ GigaChat, убрать строку из `authorized_keys`, поставить
`DEPLOY_ENABLED=false`. `docker compose down` останавливает стенд, с ключом `-v` ещё
и **безвозвратно удаляет базу**. Резервные копии автоматически не делаются.
