"""Builds app/static/i18n/en.json and ko.json from the table below («русский текст» -> English, 한국어).
Add a row, run `python3 tools/i18n_build.py`, commit the two json files. A text that is not in the table stays Russian.
`python3 tools/i18n_build.py --missing` lists the Russian texts of index.html that have no translation yet."""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
T = [
("KOREA HUB — работа, жильё и объявления в Корее", "KOREA HUB — jobs, housing and ads in Korea", "KOREA HUB — 한국의 일자리, 주거, 중고거래"),
("Всё для жизни в Корее — в одном месте", "Everything for life in Korea in one place", "한국 생활에 필요한 모든 것을 한곳에서"),
("Войти", "Sign in", "로그인"), ("Город", "City", "도시"), ("Все", "All", "전체"),
("💼 Работа", "💼 Jobs", "💼 일자리"), ("🏠 Жильё", "🏠 Housing", "🏠 주거"), ("🛍️ Барахолка", "🛍️ Marketplace", "🛍️ 중고장터"),
("Работа", "Jobs", "일자리"), ("Жильё", "Housing", "주거"), ("Барахолка", "Marketplace", "중고장터"),
("Знакомства", "Dating", "만남"), ("Сообщество", "Community", "커뮤니티"), ("Услуги", "Services", "서비스"),
("Автомобили", "Cars", "자동차"), ("Виза и документы", "Visa & documents", "비자 및 서류"), ("Полезная информация", "Useful info", "유용한 정보"),
("Ещё", "More", "더보기"), ("Свежие вакансии", "New jobs", "새 일자리"), ("Все вакансии ›", "All jobs ›", "전체 일자리 ›"),
("Новые объявления", "New listings", "새 게시글"), ("Загружаем…", "Loading…", "불러오는 중…"),
("Сначала новые", "Newest first", "최신순"), ("По совпадению", "Best match", "관련도순"), ("Сначала дешёвые", "Cheapest first", "낮은 가격순"),
("Сначала дорогие", "Most expensive first", "높은 가격순"), ("Показать ещё", "Show more", "더 보기"), ("← Назад", "← Back", "← 뒤로"),
("СОХРАНЁННОЕ", "SAVED", "저장됨"), ("Избранное", "Favorites", "찜 목록"), ("Вещи", "Items", "물품"),
("ПЕРЕПИСКА", "MESSAGES", "대화"), ("Сообщения", "Messages", "메시지"), ("Мне написали", "Wrote to me", "받은 문의"),
("Я написал(а)", "I wrote", "보낸 문의"), ("Непрочитанные", "Unread", "읽지 않음"), ("Чат", "Chat", "채팅"),
("🔗 Прислать объявление", "🔗 Send a listing", "🔗 게시글 보내기"), ("📌 Закрепить", "📌 Pin", "📌 고정"), ("🗂 Скрыть чат", "🗂 Hide chat", "🗂 채팅 숨기기"),
("🚫 Заблокировать", "🚫 Block", "🚫 차단"), ("⚑ Пожаловаться", "⚑ Report", "⚑ 신고"), ("Открыть ›", "Open ›", "열기 ›"),
("Мой профиль", "My profile", "내 프로필"), ("виза не указана", "visa not set", "비자 미입력"), ("✔ Работодатель", "✔ Employer", "✔ 고용주"),
("➕ Добавить объявление", "➕ Add a listing", "➕ 게시글 등록"), ("Мои кабинеты", "My accounts", "내 계정"), ("Личный", "Personal", "개인"),
("Баланс: …", "Balance: …", "잔액: …"), ("Пополнить", "Top up", "충전"), ("Компания", "Company", "회사"),
("Для работодателей", "For employers", "고용주용"), ("Открыть", "Open", "열기"), ("Анкета, симпатии", "Profile, likes", "프로필, 좋아요"),
("Объявления", "Listings", "게시글"), ("Отклики", "Applications", "지원"), ("Моя активность", "My activity", "내 활동"),
("Мои объявления", "My listings", "내 게시글"), ("Мои отклики", "My applications", "내 지원"), ("Мои поиски", "My searches", "내 검색"),
("Пригласить друзей", "Invite friends", "친구 초대"), ("Моё резюме (ищу работу)", "My CV (looking for work)", "내 이력서 (구직)"),
("Найти сотрудника (для работодателей)", "Find an employee (for employers)", "직원 찾기 (고용주용)"), ("Аккаунт", "Account", "계정"),
("Настройки", "Settings", "설정"), ("История платежей", "Payment history", "결제 내역"), ("Заблокированные", "Blocked", "차단 목록"),
("Помощь и данные", "Help & data", "도움말 및 데이터"), ("Написать в поддержку", "Contact support", "고객 지원 문의"), ("Вопросы и ответы", "FAQ", "자주 묻는 질문"),
("Недавно смотрел", "Recently viewed", "최근 본 글"), ("Мои отзывы", "My reviews", "내 후기"), ("Правила", "Rules", "이용 규칙"),
("Выгрузить мои данные", "Export my data", "내 데이터 내보내기"), ("Выйти", "Sign out", "로그아웃"), ("Удалить аккаунт", "Delete account", "계정 삭제"),
("Уведомления", "Notifications", "알림"), ("Прочитать все", "Mark all as read", "모두 읽음"), ("Выбрать", "Select", "선택"),
("🔄 Продлить", "🔄 Renew", "🔄 연장"), ("Закрыть", "Close", "닫기"), ("Удалить", "Delete", "삭제"),
("Активные", "Active", "진행 중"), ("На проверке", "In review", "검토 중"), ("Черновики", "Drafts", "임시저장"), ("Закрытые", "Closed", "종료됨"),
("💗 Знакомства", "💗 Dating", "💗 만남"), ("Моя анкета", "My profile", "내 프로필"), ("Девушки", "Women", "여성"), ("Парни", "Men", "남성"),
("Любой возраст", "Any age", "모든 연령"), ("36 и старше", "36 and older", "36세 이상"), ("Мне симпатизируют", "Likes me", "나를 좋아함"),
("Я симпатирую", "I like", "내가 좋아함"), ("Взаимные", "Mutual", "서로 좋아함"), ("🟢 Недавно онлайн", "🟢 Recently online", "🟢 최근 접속"),
("⚙ Ещё фильтры", "⚙ More filters", "⚙ 필터 더보기"), ("👥 Сообщество", "👥 Community", "👥 커뮤니티"), ("Национальности", "Nationalities", "국적"),
("Города", "Cities", "도시"), ("📘 Гид по Корее", "📘 Guide to Korea", "📘 한국 가이드"), ("Главная", "Home", "홈"), ("Добавить", "Add", "등록"),
("Профиль", "Profile", "프로필"), ("Добро пожаловать", "Welcome", "환영합니다"),
("Чтобы публиковать объявления, писать и покупать услуги, примите правила:", "To post listings, write messages and buy services, please accept the rules:", "게시글 등록, 메시지, 서비스 구매를 위해 규칙에 동의해 주세요:"),
("Я принимаю", "I accept the", "다음에 동의합니다:"), ("пользовательское соглашение", "terms of use", "이용약관"),
("Я согласен на обработку данных по", "I agree to data processing under the", "다음에 따른 개인정보 처리에 동의합니다:"), ("политике конфиденциальности", "privacy policy", "개인정보 처리방침"),
("Продолжить", "Continue", "계속"), ("Что добавить?", "What to add?", "무엇을 등록할까요?"), ("💼 Вакансия", "💼 Job", "💼 구인"), ("🛍️ Товар", "🛍️ Item", "🛍️ 물품"),
("🚗 Автомобиль", "🚗 Car", "🚗 자동차"), ("🛠️ Услуга", "🛠️ Service", "🛠️ 서비스"), ("💗 Анкета знакомств", "💗 Dating profile", "💗 만남 프로필"),
("Из шаблона", "From a template", "템플릿에서"), ("Новое объявление", "New listing", "새 게시글"), ("Раздел", "Section", "분류"), ("Заголовок", "Title", "제목"),
("Регион", "Region", "지역"), ("Город / район", "City / district", "도시 / 구"), ("Сумма, ₩", "Amount, ₩", "금액, ₩"), ("Пометка к цене", "Price note", "가격 메모"),
("Цена договорная", "Price negotiable", "가격 협의"), ("Должность", "Position", "직무"), ("Оплата", "Pay period", "급여 단위"), ("Зарплата до, ₩", "Salary up to, ₩", "급여 상한, ₩"),
("Занятость", "Employment", "고용 형태"), ("График", "Schedule", "근무 시간"), ("Подходящие визы", "Suitable visas", "가능한 비자"), ("Мест", "Openings", "모집 인원"),
("Начало работы", "Start date", "근무 시작일"), ("Жильё предоставляется", "Housing provided", "숙소 제공"), ("Питание включено", "Meals included", "식사 제공"),
("Есть развозка", "Transport provided", "통근 버스 있음"), ("Нужен опыт", "Experience required", "경력 필요"), ("Тип жилья", "Housing type", "주거 유형"),
("Залог, ₩", "Deposit, ₩", "보증금, ₩"), ("Коммунальные, ₩", "Utilities, ₩", "관리비, ₩"), ("Площадь, м²", "Area, m²", "면적, m²"), ("Комнат", "Rooms", "방 수"),
("Этаж", "Floor", "층"), ("Свободно с", "Available from", "입주 가능일"), ("Срок договора", "Lease term", "계약 기간"), ("Ближайшая станция метро", "Nearest subway station", "가까운 지하철역"),
("Можно с животными", "Pets allowed", "반려동물 가능"), ("С мебелью", "Furnished", "가구 포함"), ("Категория", "Category", "카테고리"), ("Состояние", "Condition", "상태"),
("Бренд", "Brand", "브랜드"), ("Модель", "Model", "모델"), ("Год выпуска", "Year", "연식"), ("Пробег, км", "Mileage, km", "주행거리, km"),
("Где оказываете услугу", "Where you provide the service", "서비스 제공 지역"), ("Опыт, лет", "Experience, years", "경력, 년"), ("Торг возможен", "Negotiable", "흥정 가능"),
("Есть доставка", "Delivery available", "배송 가능"), ("Описание", "Description", "설명"), ("Фото (до 10)", "Photos (up to 10)", "사진 (최대 10장)"),
("Для жилья и товаров нужно хотя бы одно фото.", "Housing and items need at least one photo.", "주거와 물품은 사진이 한 장 이상 필요합니다."),
("Контакт для связи", "Contact", "연락처"),
("Написать вам можно и в чате приложения. Телефон и @username — только в этом поле, не в описании.", "People can also write to you in the app chat. Put a phone or @username only in this field, not in the description.", "앱 채팅으로도 연락할 수 있습니다. 전화번호나 @username은 설명이 아닌 이 칸에만 적어 주세요."),
("Так увидят ваше объявление", "This is how your listing will look", "게시글이 이렇게 보입니다"), ("📑 Сохранить как шаблон", "📑 Save as template", "📑 템플릿으로 저장"),
("Подтверждаю: всё правда, предоплат и переводов на карту не прошу", "I confirm: it is all true, and I do not ask for prepayments or card transfers", "모든 내용이 사실이며 선결제나 계좌 이체를 요구하지 않음을 확인합니다"),
("Модератор проверит объявление, обычно в течение суток. Оно будет показано 30 дней, потом его можно продлить.", "A moderator will review the listing, usually within a day. It is shown for 30 days, then you can renew it.", "운영자가 보통 하루 안에 검토합니다. 30일간 게시되며 이후 연장할 수 있습니다."),
("Опубликовать", "Publish", "게시"), ("Сохранить черновик", "Save draft", "임시저장"), ("💾 Сохранить", "💾 Save", "💾 저장"), ("Далее", "Next", "다음"),
("Отправлено на проверку", "Sent for review", "검토 요청됨"), ("Мы сообщим в Telegram, когда модератор примет решение.", "We will tell you in Telegram when a moderator decides.", "운영자가 결정하면 텔레그램으로 알려드립니다."),
("Добавить ещё", "Add another", "하나 더 등록"), ("Фильтры", "Filters", "필터"), ("Цена от, ₩", "Price from, ₩", "가격 최소, ₩"), ("Цена до, ₩", "Price to, ₩", "가격 최대, ₩"),
("Виза", "Visa", "비자"), ("Залог до, ₩", "Deposit up to, ₩", "보증금 최대, ₩"), ("Площадь от, м²", "Area from, m²", "면적 최소, m²"), ("Комнат не меньше", "At least rooms", "최소 방 수"),
("Любое", "Any", "상관없음"), ("Год от", "Year from", "연식 최소"), ("Год до", "Year to", "연식 최대"), ("Пробег до, км", "Mileage up to, km", "주행거리 최대, km"),
("Выберите раздел «Работа», «Жильё» или «Барахолка», чтобы появились дополнительные фильтры.", "Choose Jobs, Housing or Marketplace to see more filters.", "추가 필터를 보려면 일자리, 주거 또는 중고장터를 선택하세요."),
("Показать", "Show", "보기"), ("Сбросить", "Reset", "초기화"), ("🔔 Следить за этим поиском", "🔔 Follow this search", "🔔 이 검색 알림 받기"),
("Причина жалобы", "Reason for the report", "신고 사유"), ("Сообщение", "Message", "메시지"), ("Отправить", "Send", "보내기"), ("Отзыв об авторе", "Review of the author", "작성자 후기"),
("Отправить отзыв", "Send review", "후기 보내기"), ("Отмена", "Cancel", "취소"), ("Да", "Yes", "예"), ("Анкета", "Profile", "프로필"),
("👁 Так вашу анкету видят другие люди. Контактов в ней нет.", "👁 This is how others see your profile. There are no contacts in it.", "👁 다른 사람들에게 이렇게 보입니다. 연락처는 포함되어 있지 않습니다."),
("♡ Нравится", "♡ Like", "♡ 좋아요"), ("Фильтры знакомств", "Dating filters", "만남 필터"), ("Кого ищет анкета", "Looking for", "찾는 상대"), ("Не важно", "Doesn't matter", "상관없음"),
("Женщину", "A woman", "여성"), ("Мужчину", "A man", "남성"), ("Цель", "Goal", "목적"), ("Любая", "Any", "상관없음"), ("Дружба", "Friendship", "친구"),
("Отношения", "Relationship", "연애"), ("Брак", "Marriage", "결혼"), ("Общение", "Chatting", "대화"), ("Рост от, см", "Height from, cm", "키 최소, cm"), ("до, см", "to, cm", "최대, cm"),
("Только подтверждённые (селфи)", "Verified only (selfie)", "인증된 프로필만 (셀카)"), ("Только VIP", "VIP only", "VIP만"), ("С приватными фото", "With private photos", "비공개 사진 있음"),
("Понятно", "Got it", "확인"), ("Продвижение", "Promotion", "홍보"), ("Применить", "Apply", "적용"), ("Редактировать анкету", "Edit profile", "프로필 수정"), ("Кабинет", "Dashboard", "내 공간"),
("👤 О вас", "👤 About you", "👤 내 소개"), ("Имя", "Name", "이름"), ("видно в анкете, без ссылок и телефона", "shown in the profile, no links or phone", "프로필에 표시됩니다. 링크와 전화번호는 넣지 마세요"),
("Дата рождения", "Date of birth", "생년월일"), ("Пол", "Gender", "성별"), ("Женщина", "Woman", "여성"), ("Мужчина", "Man", "남성"), ("Рост, см", "Height, cm", "키, cm"), ("Не указана", "Not set", "미입력"),
("💞 Что вы ищете", "💞 What you are looking for", "💞 찾는 상대"), ("Кого ищу", "Looking for", "찾는 상대"), ("Не указано", "Not set", "미입력"), ("Цель знакомства", "Purpose", "만남 목적"), ("О себе", "About me", "자기소개"),
("📷 Фото", "📷 Photos", "📷 사진"), ("Главное фото", "Main photo", "대표 사진"), ("JPEG, PNG или WebP, до 5 МБ. Видно всем после проверки", "JPEG, PNG or WebP, up to 5 MB. Visible to everyone after review", "JPEG, PNG, WebP, 5MB 이하. 검토 후 모두에게 공개됩니다"),
("Фото-подтверждение", "Photo verification", "사진 인증"), ("по желанию, даёт отметку ✔", "optional, gives a ✔ mark", "선택 사항, ✔ 표시가 붙습니다"),
("После правки анкета проходит проверку модератором. Контакты в анкете не нужны: общение только в приложении.", "After an edit the profile is reviewed by a moderator. No contacts are needed: chat only in the app.", "수정 후 운영자가 다시 검토합니다. 연락처는 필요 없으며 대화는 앱에서만 합니다."),
("Сохранить", "Save", "저장"), ("Мой город", "My city", "내 도시"), ("Отклик на вакансию", "Apply for the job", "구인 지원"), ("Как с вами связаться", "How to contact you", "연락 방법"),
("Подставить из моего резюме", "Fill from my CV", "내 이력서에서 가져오기"), ("Сообщение работодателю", "Message to the employer", "고용주에게 메시지"),
("Работодатель увидит ваш контакт, город и визу из профиля. Telegram ID не передаётся.", "The employer will see your contact, city and visa from your profile. Your Telegram ID is not shared.", "고용주는 프로필의 연락처, 도시, 비자를 볼 수 있습니다. 텔레그램 ID는 전달되지 않습니다."),
("Откликнуться", "Apply", "지원하기"), ("Заявка работодателя", "Employer application", "고용주 신청"), ("Название компании", "Company name", "회사 이름"),
("Регистрационный номер компании", "Company registration number", "사업자등록번호"), ("Контактное лицо", "Contact person", "담당자"),
("Заявку проверит модератор. После одобрения в профиле появится отметка работодателя.", "A moderator will review the application. After approval your profile gets an employer mark.", "운영자가 신청을 검토합니다. 승인되면 프로필에 고용주 표시가 붙습니다."),
("Отправить заявку", "Send application", "신청하기"), ("Завершите регистрацию", "Finish registration", "가입 완료"),
("видно в откликах и чатах, без ссылок и телефона", "shown in applications and chats, no links or phone", "지원서와 채팅에 표시됩니다. 링크와 전화번호는 넣지 마세요"),
("Текущий пароль", "Current password", "현재 비밀번호"), ("Пароль", "Password", "비밀번호"), ("от 8 знаков, буквы и цифры", "8+ characters, letters and digits", "8자 이상, 문자와 숫자"),
("Пароль ещё раз", "Repeat password", "비밀번호 확인"), ("Показать пароль", "Show password", "비밀번호 보기"), ("Заполню позже", "Later", "나중에"), ("Вход на сайт", "Sign in to the site", "사이트 로그인"),
("По e-mail и паролю", "With e-mail and password", "이메일과 비밀번호"), ("Через Telegram", "With Telegram", "텔레그램으로"),
("Нажмите кнопку, откроется Telegram. Там нажмите «Это я, войти» — сайт войдёт сам.", "Press the button and Telegram opens. Tap “It's me, sign in” there and the site signs in by itself.", "버튼을 누르면 텔레그램이 열립니다. “나예요, 로그인”을 누르면 사이트가 자동으로 로그인됩니다."),
("Войти через Telegram", "Sign in with Telegram", "텔레그램으로 로그인"), ("Открыть Telegram вручную", "Open Telegram manually", "텔레그램 직접 열기"),
("Нет связи с интернетом. Показано то, что уже загружено", "No internet connection. Showing what is already loaded", "인터넷에 연결되어 있지 않습니다. 이미 불러온 내용만 표시됩니다"),
("Язык", "Language", "언어"), ("Поиск вакансий, жилья, товаров и услуг…", "Search jobs, housing, items and services…", "일자리, 주거, 물품, 서비스 검색…"), ("Назад", "Back", "뒤로"),
("Сортировка", "Sort", "정렬"), ("Поиск по перепискам: имя, объявление, текст", "Search chats: name, listing, text", "대화 검색: 이름, 게시글, 내용"), ("Действия", "Actions", "작업"),
("Быстрые ответы", "Quick replies", "빠른 답장"), ("Прикрепить фото", "Attach a photo", "사진 첨부"), ("Прикрепить PDF или аудио", "Attach PDF or audio", "PDF 또는 오디오 첨부"),
("Голосовое сообщение", "Voice message", "음성 메시지"), ("Изменить город", "Change city", "도시 변경"), ("Возраст", "Age", "나이"), ("Поиск по статьям", "Search articles", "글 검색"),
("Например: Работа на заводе", "For example: Factory work", "예: 공장 근무"), ("в месяц, торг", "per month, negotiable", "월, 협의 가능"), ("Оператор, упаковщик…", "Operator, packer…", "작업자, 포장…"),
("Например: 1 год", "For example: 1 year", "예: 1년"), ("Например: Каннам", "For example: Gangnam", "예: 강남"), ("Например: Samsung", "For example: Samsung", "예: Samsung"), ("Например: Sonata", "For example: Sonata", "예: Sonata"),
("@username или телефон", "@username or phone", "@username 또는 전화번호"), ("Можно оставить пустым", "Can be left empty", "비워 둘 수 있습니다"), ("Оценка", "Rating", "평점"),
("Просмотр фото", "Photo viewer", "사진 보기"), ("Дальше", "Next", "다음"), ("Есть промокод?", "Have a promo code?", "프로모션 코드가 있나요?"), ("Например: Аня", "For example: Anna", "예: 안나"),
("Например: Иван", "For example: Ivan", "예: 이반"),
("Забыли пароль? Войдите через Telegram и задайте новый в «Настройки → Вход на сайт».", "Forgot your password? Sign in with Telegram and set a new one in Settings → Sign in to the site.", "비밀번호를 잊으셨나요? 텔레그램으로 로그인한 뒤 설정 → 사이트 로그인에서 새로 설정하세요."),
("Например: Samin Motors", "For example: Samin Motors", "예: Samin Motors"),
("Например: Сеул и Кёнги, онлайн", "For example: Seoul and Gyeonggi, online", "예: 서울·경기, 온라인"),
("Подробности. Телефон и ссылки пишите в поле «Контакт»", "Details. Put phone and links in the Contact field", "자세한 내용. 전화번호와 링크는 연락처 칸에 적어 주세요"),
("Что понравилось или не понравилось? Без телефонов и ссылок", "What did you like or not like? No phones or links", "좋았던 점 또는 아쉬운 점? 전화번호와 링크는 넣지 마세요"),
("Чем занимаетесь, что любите, кого хотите встретить (от 40 знаков — анкета получает больше симпатий)", "What you do, what you love, who you want to meet (40+ characters get more likes)", "하는 일, 좋아하는 것, 만나고 싶은 사람 (40자 이상이면 더 많은 좋아요를 받습니다)"),
("Опыт, когда можете начать", "Your experience, when you can start", "경력, 시작 가능한 시기"),
# --- texts that the app draws itself (lists, statuses, collections): exact matches only ---
("На модерации", "In moderation", "검토 중"), ("Опубликовано", "Published", "게시됨"), ("Отклонено", "Rejected", "반려됨"), ("Нужно исправить", "Needs a fix", "수정 필요"),
("Скрыто", "Hidden", "숨김"), ("Продано", "Sold", "판매 완료"), ("В архиве", "Archived", "보관됨"), ("Черновик", "Draft", "임시저장"), ("Срок истёк", "Expired", "기간 만료"),
("Подробнее", "Details", "자세히"), ("Похожие объявления", "Similar listings", "비슷한 게시글"), ("Написать", "Message", "문의하기"), ("💬 Написать", "💬 Message", "💬 문의하기"),
("Пожаловаться", "Report", "신고"), ("⚑ Пожаловаться", "⚑ Report", "⚑ 신고"), ("Описание", "Description", "설명"), ("Показать ещё", "Show more", "더 보기"),
("🏠 Работа с жильём", "🏠 Jobs with housing", "🏠 숙소 제공 일자리"), ("🍱 Работа с питанием", "🍱 Jobs with meals", "🍱 식사 제공 일자리"), ("🛂 Работа под мою визу", "🛂 Jobs for my visa", "🛂 내 비자에 맞는 일자리"),
("💴 Жильё без залога", "💴 Housing with no deposit", "💴 보증금 없는 주거"), ("🪑 Жильё с мебелью", "🪑 Furnished housing", "🪑 가구 포함 주거"), ("🐾 Можно с животными", "🐾 Pets allowed", "🐾 반려동물 가능"),
("📉 Дешёвые вещи", "📉 Cheap items", "📉 저렴한 물품"), ("📍 В моём городе", "📍 In my city", "📍 내 도시"),
("Первая неделя в Корее", "Your first week in Korea", "한국 정착 첫 주"), ("Скрыть", "Hide", "숨기기"), ("Развернуть чек-лист ▾", "Show the checklist ▾", "체크리스트 펼치기 ▾"),
("🛂 Проверить визу и документы", "🛂 Check your visa and documents", "🛂 비자와 서류 확인"), ("📍 Зарегистрировать адрес и получить ARC", "📍 Register your address and get an ARC", "📍 주소 등록 및 외국인등록증 발급"),
("🏦 Банк и SIM-карта", "🏦 Bank and SIM card", "🏦 은행 및 유심"), ("🏥 Медицинская страховка", "🏥 Health insurance", "🏥 건강보험"), ("🏠 Найти жильё", "🏠 Find housing", "🏠 주거 찾기"), ("💼 Найти работу", "💼 Find a job", "💼 일자리 찾기"),
("Качество объявления", "Listing quality", "게시글 품질"), ("Нет сообщений", "No messages", "메시지 없음"), ("Вы: ", "You: ", "나: "),
("Пока нет объявлений. Добавьте первое кнопкой «+».", "No listings yet. Add the first one with the “+” button.", "아직 게시글이 없습니다. “+” 버튼으로 첫 글을 등록하세요."),
("Ничего не нашли. Проверьте написание или сбросьте фильтры.", "Nothing found. Check the spelling or reset the filters.", "결과가 없습니다. 철자를 확인하거나 필터를 초기화하세요."),
("Продвинуть", "Promote", "홍보하기"), ("🚀 Продвинуть", "🚀 Promote", "🚀 홍보하기"), ("✏️ Изменить", "✏️ Edit", "✏️ 수정"), ("⚡ Цена", "⚡ Price", "⚡ 가격"), ("⧉ Копия", "⧉ Copy", "⧉ 복사"),
("🔀 Перенести", "🔀 Move", "🔀 이동"), ("Главное", "Main", "주요"), ("История", "History", "이력"), ("Цена и срок", "Price and term", "가격 및 기간"), ("Копии", "Copies", "복사"), ("Статус", "Status", "상태"),
("Просмотров", "Views", "조회수"), ("В избранном", "In favorites", "찜"), ("Конверсия", "Conversion", "전환율"), ("Тема", "Theme", "테마"), ("Светлая", "Light", "라이트"), ("Тёмная", "Dark", "다크"), ("Как в Telegram", "Like Telegram", "텔레그램과 동일"),
("Баланс", "Balance", "잔액"), ("Мой баланс", "My balance", "내 잔액"), ("Показано то, что уже загружено", "Showing what is already loaded", "이미 불러온 내용만 표시됩니다"),
("Основная навигация", "Main navigation", "주요 탐색"),
]
OUT = ROOT / "app" / "static" / "i18n"


def missing() -> list[str]:
    s = re.sub(r"<script.*?</script>", "", (ROOT / "app/static/index.html").read_text(), flags=re.S)
    found = []
    for pat in (r">([^<>]{2,120})<", r'(?:placeholder|aria-label|title)="([^"]{2,140})"'):
        for m in re.finditer(pat, s):
            t = html.unescape(m.group(1)).strip()
            if re.search(r"[А-Яа-я]", t) and t not in found:
                found.append(t)
    known = {r for r, _, _ in T}
    return [t for t in found if t not in known]


if __name__ == "__main__":
    if "--missing" in sys.argv:
        print("\n".join(missing()))
        sys.exit(0)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "en.json").write_text(json.dumps({r: e for r, e, _ in T}, ensure_ascii=False, indent=0), encoding="utf-8")
    (OUT / "ko.json").write_text(json.dumps({r: k for r, _, k in T}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(T)} texts written; still without a translation: {len(missing())}")
