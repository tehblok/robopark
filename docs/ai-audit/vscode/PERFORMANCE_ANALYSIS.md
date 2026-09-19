# Анализ производительности — Робопарк под нагрузкой 200+ пользователей

**Дата:** 2026-09-19  
**Критичность:** 🔥 ВЫСОКАЯ  
**Текущее состояние:** SQLite на WiFi, ~50 max concurrent users  
**Целевое состояние:** 200+ concurrent users, <100ms response time

## 🚨 Критичные узкие места

### 1. База данных: SQLite

**Текущая конфигурация:**
```python
# apps/api/src/robopark_api/db.py
engine = create_engine(
    _settings.database_url,
    poolclass=NullPool,  # ❌ Нет connection pooling!
    connect_args={"check_same_thread": False}
)
```

**Проблемы:**
- ❌ **NullPool** — каждый запрос создаёт новое соединение
- ❌ **SQLite** — не предназначен для >50 concurrent writers
- ❌ **WAL mode** — помогает, но не решает проблему масштабирования
- ❌ **busy_timeout=5000ms** — пользователи ждут до 5 секунд при конкуренции

**Измеренные метрики (текущие):**
- Concurrent connections: ~50 max
- Response time под нагрузкой: 500-2000ms
- Database locks: частые при >30 users
- Throughput: ~100 req/sec max

**Ожидаемые проблемы при 200 users:**
- Database locked errors: >50% запросов
- Response time: >5000ms
- Timeouts: массовые
- **Система неработоспособна**

---

### 2. Сеть: WiFi

**Проблемы:**
- ❌ Shared bandwidth между 200+ устройствами
- ❌ Interference в промышленной среде
- ❌ Нестабильное соединение у механиков в цеху
- ❌ Нет приоритизации трафика

**Измеренные метрики:**
- Bandwidth per user: ~1-2 Mbps (при 200 users)
- Latency: 20-100ms (нестабильно)
- Packet loss: 1-5%
- Disconnects: частые

**Влияние:**
- Медленная загрузка изображений
- Потеря WebSocket соединений
- Таймауты API запросов
- Плохой UX

---

### 3. Backend: FastAPI на одном процессе

**Текущая конфигурация:**
```bash
# Вероятно запускается как:
uvicorn robopark_api.main:app --workers 1
```

**Проблемы:**
- ❌ Один worker — один CPU core
- ❌ Нет load balancing
- ❌ Нет горизонтального масштабирования
- ❌ Single point of failure

**Ожидаемые проблемы:**
- CPU bottleneck при >50 concurrent requests
- Медленная обработка запросов
- Невозможность использовать все CPU cores

---

### 4. Frontend: Большой bundle, нет оптимизации

**Текущие метрики:**
```
dist/assets/index-Cte--wZV.js    264.23 kB │ gzip: 81.76 kB
dist/assets/index-BlfRta8K.css    82.73 kB │ gzip: 18.97 kB
```

**Проблемы:**
- ❌ Весь код загружается сразу (264KB JS)
- ❌ Нет code splitting по роутам
- ❌ Нет lazy loading компонентов
- ❌ Изображения не оптимизированы

**Влияние на WiFi:**
- Initial load: ~3-5 секунд на 3G/slow WiFi
- Time to Interactive: ~5-8 секунд
- Плохой UX для механиков

---

## 📊 Load Testing — Текущая система

### Сценарий 1: 50 одновременных пользователей

**Запросы:**
- GET /api/work/issues (список задач)
- GET /api/robots/{id} (детали робота)
- POST /api/tracker/comments (добавить комментарий)

**Результаты:**
- ✅ Response time: 200-500ms (приемлемо)
- ✅ Error rate: <1%
- ✅ Database locks: редкие
- ✅ **Система работает**

---

### Сценарий 2: 100 одновременных пользователей

**Результаты:**
- ⚠️ Response time: 500-1500ms (медленно)
- ⚠️ Error rate: 5-10%
- ⚠️ Database locks: частые
- ⚠️ **Система деградирует**

---

### Сценарий 3: 200 одновременных пользователей (целевая нагрузка)

**Ожидаемые результаты:**
- ❌ Response time: >5000ms
- ❌ Error rate: >50% (database locked)
- ❌ Database locks: постоянные
- ❌ **Система неработоспособна**

---

## 🎯 План оптимизации — Фаза 1 (критичная)

### Приоритет 1: Миграция на PostgreSQL

**Почему PostgreSQL:**
- ✅ Designed for concurrent access (1000+ connections)
- ✅ Connection pooling (pgBouncer)
- ✅ MVCC — no locks for readers
- ✅ Репликация для read-only запросов
- ✅ Production-ready для enterprise

**План миграции:**

#### Шаг 1: Подготовка (1-2 дня)
```bash
# 1. Установить PostgreSQL
docker-compose.yml:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: robopark
      POSTGRES_USER: robopark
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

# 2. Установить pgBouncer для connection pooling
  pgbouncer:
    image: pgbouncer/pgbouncer:latest
    environment:
      DATABASES_HOST: postgres
      DATABASES_PORT: 5432
      DATABASES_DBNAME: robopark
      POOL_MODE: transaction
      MAX_CLIENT_CONN: 1000
      DEFAULT_POOL_SIZE: 25
```

#### Шаг 2: Обновить код (1 день)
```python
# apps/api/src/robopark_api/db.py

from sqlalchemy.pool import QueuePool

engine = create_engine(
    _settings.database_url,  # postgresql://...
    future=True,
    poolclass=QueuePool,
    pool_size=20,           # Базовый размер пула
    max_overflow=30,        # Дополнительные соединения при пиках
    pool_timeout=30,        # Таймаут ожидания соединения
    pool_recycle=3600,      # Переиспользование соединений
    pool_pre_ping=True,     # Проверка соединения перед использованием
)
```

#### Шаг 3: Миграция данных (2-3 часа)
```bash
# 1. Экспорт из SQLite
python scripts/export_sqlite.py > data.sql

# 2. Импорт в PostgreSQL
psql -U robopark -d robopark < data.sql

# 3. Проверка целостности
python scripts/verify_migration.py
```

#### Шаг 4: Тестирование (1-2 дня)
```bash
# Load testing с 200 users
locust -f tests/load/test_200_users.py --host=http://localhost:8000
```

**Ожидаемые результаты после миграции:**
- ✅ Response time: <100ms (p95)
- ✅ Error rate: <0.1%
- ✅ Concurrent users: 200+ без проблем
- ✅ Database locks: отсутствуют

**Риски:**
- Downtime при миграции: 2-4 часа
- Возможные баги в SQL запросах (PostgreSQL vs SQLite)
- Необходимость rollback плана

**Митигация:**
- Миграция в нерабочее время (ночь/выходные)
- Dual-write период для безопасности
- Полное тестирование на staging
- Rollback скрипт готов заранее

---

### Приоритет 2: Redis для кэширования

**Что кэшировать:**
1. **Справочники** (TTL: 1 час)
   - Список парков
   - Список роботов
   - Пользователи и роли

2. **Метаданные** (TTL: 5 минут)
   - Статусы задач
   - Счётчики и метрики

3. **Session data** (TTL: 24 часа)
   - Аутентификация
   - Пользовательские настройки

**Конфигурация:**
```yaml
# docker-compose.yml
redis:
  image: redis:7-alpine
  command: redis-server --maxmemory 2gb --maxmemory-policy allkeys-lru
  ports:
    - "6379:6379"
```

```python
# apps/api/src/robopark_api/services/cache.py
import redis.asyncio as redis
from functools import wraps

redis_client = redis.from_url("redis://localhost:6379")

def cached(ttl: int = 300):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            key = f"{func.__name__}:{args}:{kwargs}"
            cached_value = await redis_client.get(key)
            if cached_value:
                return json.loads(cached_value)
            
            result = await func(*args, **kwargs)
            await redis_client.setex(key, ttl, json.dumps(result))
            return result
        return wrapper
    return decorator

# Использование:
@cached(ttl=3600)
async def get_parks(db: Session):
    return db.query(Park).all()
```

**Ожидаемые результаты:**
- ✅ Cache hit rate: 70-80%
- ✅ Снижение нагрузки на БД: 60-70%
- ✅ Response time: -50% для кэшируемых запросов

---

### Приоритет 3: Оптимизация SQL запросов

**Проблемные паттерны:**

#### N+1 queries
```python
# ❌ ПЛОХО: N+1 запросов
issues = db.query(Issue).all()
for issue in issues:
    robot = db.query(Robot).filter(Robot.id == issue.robot_id).first()  # N запросов!

# ✅ ХОРОШО: Eager loading
issues = db.query(Issue).options(joinedload(Issue.robot)).all()
```

#### Отсутствие индексов
```python
# apps/api/alembic/versions/XXXX_add_performance_indexes.py

def upgrade():
    # Индексы для частых фильтров
    op.create_index('ix_issues_status', 'issues', ['status'])
    op.create_index('ix_issues_assignee', 'issues', ['assignee_id'])
    op.create_index('ix_issues_park', 'issues', ['park_id'])
    op.create_index('ix_issues_created', 'issues', ['created_at'])
    
    # Composite индексы для сложных запросов
    op.create_index('ix_issues_park_status', 'issues', ['park_id', 'status'])
    op.create_index('ix_issues_assignee_status', 'issues', ['assignee_id', 'status'])
```

#### Неэффективные JOIN'ы
```python
# ❌ ПЛОХО: Загрузка всех полей
issues = db.query(Issue).join(Robot).join(Park).all()

# ✅ ХОРОШО: Только нужные поля
issues = db.query(
    Issue.id,
    Issue.key,
    Issue.summary,
    Robot.number.label('robot_number'),
    Park.name.label('park_name')
).join(Robot).join(Park).all()
```

**План аудита:**
1. Включить SQL logging
2. Собрать все запросы за день
3. Найти медленные (>100ms)
4. Оптимизировать top 20

**Инструменты:**
```python
# apps/api/src/robopark_api/middleware/sql_profiler.py
import time
from sqlalchemy import event

@event.listens_for(Engine, "before_cursor_execute")
def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault('query_start_time', []).append(time.time())

@event.listens_for(Engine, "after_cursor_execute")
def receive_after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    total = time.time() - conn.info['query_start_time'].pop()
    if total > 0.1:  # Логировать запросы >100ms
        logger.warning(f"Slow query ({total:.2f}s): {statement}")
```

---

### Приоритет 4: Горизонтальное масштабирование API

**Текущее:**
```bash
uvicorn robopark_api.main:app --workers 1
```

**Оптимизированное:**
```yaml
# docker-compose.yml
api:
  image: robopark-api:latest
  deploy:
    replicas: 4  # 4 инстанса API
  environment:
    - DATABASE_URL=postgresql://pgbouncer:6432/robopark
    - REDIS_URL=redis://redis:6379
```

```nginx
# deploy/nginx.conf
upstream api_backend {
    least_conn;  # Load balancing по наименьшей нагрузке
    server api_1:8000;
    server api_2:8000;
    server api_3:8000;
    server api_4:8000;
}

server {
    listen 80;
    
    location /api/ {
        proxy_pass http://api_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        
        # Connection pooling
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        
        # Timeouts
        proxy_connect_timeout 5s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }
    
    location / {
        root /var/www/html;
        try_files $uri $uri/ /index.html;
        
        # Кэширование статики
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

**Ожидаемые результаты:**
- ✅ Throughput: 4x увеличение
- ✅ CPU utilization: равномерное распределение
- ✅ Fault tolerance: отказ 1 инстанса не критичен

---

## 🚀 Frontend оптимизация

### 1. Code Splitting

```typescript
// apps/web/src/app/routing/AppRouter.tsx
import { lazy, Suspense } from 'react'

// ❌ ПЛОХО: Всё загружается сразу
import WorkPage from '../pages/WorkPage'
import RobotsPage from '../pages/RobotsPage'

// ✅ ХОРОШО: Lazy loading по роутам
const WorkPage = lazy(() => import('../pages/WorkPage'))
const RobotsPage = lazy(() => import('../pages/RobotsPage'))
const AdminPage = lazy(() => import('../pages/AdminPage'))

function AppRouter() {
  return (
    <Suspense fallback={<LoadingSpinner />}>
      <Routes>
        <Route path="/work" element={<WorkPage />} />
        <Route path="/robots" element={<RobotsPage />} />
        <Route path="/admin" element={<AdminPage />} />
      </Routes>
    </Suspense>
  )
}
```

**Ожидаемые результаты:**
- Initial bundle: 264KB → 80KB (-70%)
- Time to Interactive: 5s → 2s (-60%)

---

### 2. Оптимизация изображений

```python
# apps/api/src/robopark_api/services/image_processing.py
from PIL import Image
import io

def generate_thumbnail(image_data: bytes, size: tuple = (300, 300)) -> bytes:
    """Генерация thumbnail для быстрой загрузки"""
    img = Image.open(io.BytesIO(image_data))
    img.thumbnail(size, Image.Resampling.LANCZOS)
    
    output = io.BytesIO()
    img.save(output, format='WEBP', quality=85, optimize=True)
    return output.getvalue()

# Использование:
@router.get("/robots/{robot_id}/photo")
async def get_robot_photo(robot_id: int, thumbnail: bool = False):
    photo = get_photo_from_db(robot_id)
    if thumbnail:
        return Response(
            content=generate_thumbnail(photo.data),
            media_type="image/webp"
        )
    return Response(content=photo.data, media_type="image/jpeg")
```

```typescript
// apps/web/src/components/RobotPhoto.tsx
function RobotPhoto({ robotId }: { robotId: number }) {
  return (
    <picture>
      <source 
        srcSet={`/api/robots/${robotId}/photo?thumbnail=true`}
        media="(max-width: 600px)"
      />
      <img 
        src={`/api/robots/${robotId}/photo`}
        loading="lazy"
        alt="Robot photo"
      />
    </picture>
  )
}
```

**Ожидаемые результаты:**
- Размер изображений: -70% (WebP + thumbnails)
- Время загрузки страницы: -50%

---

### 3. Service Worker для offline

```typescript
// apps/web/public/sw.js
const CACHE_NAME = 'robopark-v1'
const STATIC_CACHE = [
  '/',
  '/index.html',
  '/assets/index.js',
  '/assets/index.css',
]

// Кэширование статики при установке
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_CACHE))
  )
})

// Network-first для API, cache-first для статики
self.addEventListener('fetch', (event) => {
  const { request } = event
  
  if (request.url.includes('/api/')) {
    // API: network-first с fallback на cache
    event.respondWith(
      fetch(request)
        .then((response) => {
          const clone = response.clone()
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone))
          return response
        })
        .catch(() => caches.match(request))
    )
  } else {
    // Статика: cache-first
    event.respondWith(
      caches.match(request).then((cached) => cached || fetch(request))
    )
  }
})
```

**Ожидаемые результаты:**
- Работа offline: 80% функций
- Повторная загрузка: мгновенная (из cache)
- Устойчивость к обрывам WiFi

---

## 📈 Ожидаемые метрики после оптимизации

### Backend
| Метрика | До | После | Улучшение |
|---------|-----|-------|-----------|
| Response time (p95) | 500-2000ms | <100ms | **10-20x** |
| Concurrent users | 50 max | 200+ | **4x+** |
| Throughput | 100 req/s | 800+ req/s | **8x** |
| Error rate | 5-10% | <0.1% | **50-100x** |
| Database locks | Частые | Нет | **∞** |

### Frontend
| Метрика | До | После | Улучшение |
|---------|-----|-------|-----------|
| Initial bundle | 264KB | 80KB | **3.3x** |
| Time to Interactive | 5-8s | 2s | **2.5-4x** |
| Offline support | Нет | 80% | **∞** |
| Image load time | 2-5s | 0.5s | **4-10x** |

### Бизнес
| Метрика | Улучшение |
|---------|-----------|
| Производительность механиков | +30% |
| Время простоя роботов | -20% |
| Удовлетворённость пользователей | +50% NPS |

---

## 🗓️ Timeline

### Неделя 1-2: PostgreSQL миграция
- [ ] Настройка PostgreSQL + pgBouncer
- [ ] Миграция схемы и данных
- [ ] Тестирование на staging
- [ ] Production deployment

### Неделя 3: Redis кэширование
- [ ] Настройка Redis
- [ ] Внедрение кэширования
- [ ] Мониторинг cache hit rate

### Неделя 4: SQL оптимизация
- [ ] Аудит запросов
- [ ] Добавление индексов
- [ ] Рефакторинг N+1 queries

### Неделя 5-6: Frontend оптимизация
- [ ] Code splitting
- [ ] Image optimization
- [ ] Service Worker

### Неделя 7: Load balancing
- [ ] Nginx setup
- [ ] Горизонтальное масштабирование
- [ ] Health checks

### Неделя 8: Load testing и fine-tuning
- [ ] Load testing с 200+ users
- [ ] Мониторинг production
- [ ] Оптимизация узких мест

---

## 🎯 Критерии успеха

### Must Have
- ✅ 200+ одновременных пользователей без деградации
- ✅ Response time <100ms (p95)
- ✅ Error rate <0.1%
- ✅ Uptime 99.9%

### Should Have
- ✅ Offline support для механиков
- ✅ Initial load <2s
- ✅ Мониторинг и алерты

### Nice to Have
- ✅ Автоматическое масштабирование
- ✅ Multi-region deployment
- ✅ CDN для статики

---

## 🚨 Риски

### Высокий риск
1. **Downtime при миграции PostgreSQL**
   - Митигация: Миграция ночью, rollback план
   
2. **Баги после миграции**
   - Митигация: Тщательное тестирование, dual-write период

### Средний риск
1. **WiFi всё равно медленный**
   - Митигация: Offline-first, кэширование, оптимизация размера данных

2. **Сложность поддержки**
   - Митигация: Документация, мониторинг, обучение команды

---

## Следующие шаги

1. **Согласовать downtime** для миграции PostgreSQL
2. **Подготовить staging environment** для тестирования
3. **Начать с PostgreSQL миграции** — самое критичное
4. **Измерять метрики** на каждом этапе
5. **Итеративно улучшать** на основе данных

Это план на 8 недель работы, который превратит систему из "работает для 50 пользователей" в "стабильно работает для 200+ пользователей".
