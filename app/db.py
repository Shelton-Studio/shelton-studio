"""Base de datos (SQLite por defecto). Cada tarea usa su propia sesión."""
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings


def _id() -> str:
    return uuid.uuid4().hex[:12]


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Brand(Base):
    """Perfil de marca: lo que Shelton sabe de tu cuenta para mantener coherencia."""
    __tablename__ = "brands"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String, default="Mi marca")
    description: Mapped[str] = mapped_column(Text, default="")      # nicho, producto, propuesta
    audience: Mapped[str] = mapped_column(Text, default="")
    tone: Mapped[str] = mapped_column(Text, default="cercano y profesional")
    visual_style: Mapped[str] = mapped_column(Text, default="fotografía realista, luz natural, colores cálidos")
    pillars: Mapped[list] = mapped_column(JSON, default=list)        # temas recurrentes
    base_hashtags: Mapped[list] = mapped_column(JSON, default=list)
    platforms: Mapped[list] = mapped_column(JSON, default=lambda: ["instagram_feed"])
    autopilot_daily: Mapped[int] = mapped_column(Integer, default=0)  # posts automáticos por día
    autopilot_hour: Mapped[int] = mapped_column(Integer, default=6)   # hora local del lote diario
    autopilot_last: Mapped[str] = mapped_column(String, default="")   # fecha del último lote
    scout_every_hours: Mapped[int] = mapped_column(Integer, default=12)  # 0 = scout apagado
    scout_last: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    learning_mode: Mapped[str] = mapped_column(String, default="auto")  # auto | approve | off
    infra_mode: Mapped[str] = mapped_column(String, default="auto")     # auto | local | cloud (switch de herramientas)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Subject(Base):
    """Banco de modelos: personaje, auto, lugar o producto recurrente, con sus fotos de referencia."""
    __tablename__ = "subjects"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String, index=True)
    kind: Mapped[str] = mapped_column(String, default="person")   # person/car/place/product/pet/other
    description: Mapped[str] = mapped_column(Text, default="")    # descripción visual detallada (para prompts)
    notes: Mapped[str] = mapped_column(Text, default="")          # personalidad, historia, uso
    references: Mapped[list] = mapped_column(JSON, default=list)  # rutas de imágenes; la primera es la principal
    alternatives: Mapped[list] = mapped_column(JSON, default=list)  # intentos de referencia no elegidos
    voice_path: Mapped[str] = mapped_column(String, default="")     # muestra de su voz (para clonarla)
    voice_style: Mapped[str] = mapped_column(Text, default="")      # cómo suena: "grave, cálida, acento neutro"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Post(Base):
    """Una pieza de contenido: imagen final + texto listo para publicar."""
    __tablename__ = "posts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    brand_id: Mapped[str | None] = mapped_column(String, nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    conversation_id: Mapped[str | None] = mapped_column(String, nullable=True)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)  # si es una edición
    subject_ids: Mapped[list] = mapped_column(JSON, default=list)         # modelos del banco que aparecen
    media_type: Mapped[str] = mapped_column(String, default="image")      # image | video | reel
    video_path: Mapped[str] = mapped_column(String, default="")           # MP4 final (video/reel)
    duration: Mapped[float] = mapped_column(Float, default=0)
    voiceover: Mapped[str] = mapped_column(Text, default="")              # guion locutado
    reel_id: Mapped[str | None] = mapped_column(String, nullable=True)    # si es una escena de un reel
    made_with: Mapped[dict] = mapped_column(JSON, default=dict)           # modelo usado: {"image": ..., "video": ..., "voice": ...}
    audio_path: Mapped[str] = mapped_column(String, default="")           # pista de voz/sonido
    idea: Mapped[str] = mapped_column(Text, default="")
    platform: Mapped[str] = mapped_column(String, default="instagram_feed")
    prompt: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    raw_path: Mapped[str] = mapped_column(String, default="")     # salida del modelo
    image_path: Mapped[str] = mapped_column(String, default="")   # recortada al formato final
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    caption: Mapped[str] = mapped_column(Text, default="")
    hashtags: Mapped[list] = mapped_column(JSON, default=list)
    alt_text: Mapped[str] = mapped_column(Text, default="")
    qa_score: Mapped[float] = mapped_column(Float, default=0)
    qa_notes: Mapped[str] = mapped_column(Text, default="")
    qa_passed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Intentos que el juez puntuó más bajo. Nunca se borran: el humano puede elegir uno.
    alternatives: Mapped[list] = mapped_column(JSON, default=list)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    # queued -> generating -> ready (revisión humana) -> approved -> published  (o failed / rejected)
    # Solo una persona aprueba o rechaza. El juez opina; nunca borra ni descarta.
    status: Mapped[str] = mapped_column(String, default="queued", index=True)
    error: Mapped[str] = mapped_column(Text, default="")
    publish_log: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Job(Base):
    """Trabajo en cola: generar un post o editar uno existente."""
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String)                     # "create" | "edit"
    post_id: Mapped[str] = mapped_column(String, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)  # pending/running/done/failed
    progress: Mapped[str] = mapped_column(String, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class Campaign(Base):
    """Lote de posts planificados de una vez (p. ej. 50 diarios)."""
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    brand_id: Mapped[str | None] = mapped_column(String, nullable=True)
    brief: Mapped[str] = mapped_column(Text, default="")
    count: Mapped[int] = mapped_column(Integer, default=0)
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="planning")  # planning/running/done/failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Conversation(Base):
    """Chat con Shelton. `api_messages` es el historial exacto para Claude (solo se añade)."""
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    title: Mapped[str] = mapped_column(String, default="Nueva conversación")
    agent: Mapped[str] = mapped_column(String, default="shelton")
    engine: Mapped[str] = mapped_column(String, default="")      # motor de imagen elegido en el chat
    api_messages: Mapped[list] = mapped_column(JSON, default=list)
    display: Mapped[list] = mapped_column(JSON, default=list)   # lo que ve el usuario
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30} if settings.DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
    if settings.DATABASE_URL.startswith("sqlite"):
        with engine.connect() as c:
            c.exec_driver_sql("PRAGMA journal_mode=WAL")


def _add_missing_columns() -> None:
    """Migración simple: si actualizas Shelton, agrega las columnas nuevas sin perder datos."""
    from sqlalchemy import inspect
    insp = inspect(engine)
    with engine.begin() as c:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {col["name"] for col in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    c.exec_driver_sql(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}')


@contextmanager
def session():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_or_create_brand(s) -> Brand:
    brand = s.query(Brand).filter(Brand.active.is_(True)).first()
    if not brand:
        brand = Brand()
        s.add(brand)
        s.flush()
    return brand


class AgentProfile(Base):
    """Ficha editable de cada agente (prompt, modelo, esfuerzo). Cada guardado sube la versión."""
    __tablename__ = "agent_profiles"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    prompt: Mapped[str] = mapped_column(Text, default="")      # vacío = prompt por defecto
    model: Mapped[str] = mapped_column(String, default="")     # vacío = CLAUDE_MODEL
    effort: Mapped[str] = mapped_column(String, default="")    # vacío = el del agente
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class AgentRun(Base):
    """Cada llamada de un agente: para métricas, actividad y costos."""
    __tablename__ = "agent_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent: Mapped[str] = mapped_column(String, index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Approval(Base):
    """Acción hacia afuera (publicar, enviar...) que espera el visto bueno de una persona."""
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String, default="publish")
    title: Mapped[str] = mapped_column(Text, default="")
    agent: Mapped[str] = mapped_column(String, default="shelton")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="pending", index=True)  # pending/approved/rejected/failed
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Connection(Base):
    """Conexión a un servicio externo: motores de imagen, publicadores, webhooks, claves de API."""
    __tablename__ = "connections"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    type: Mapped[str] = mapped_column(String, index=True)       # ver app/connections.py
    name: Mapped[str] = mapped_column(String, default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)    # se guarda solo en tu máquina
    roles: Mapped[list] = mapped_column(JSON, default=list)     # generate/edit/subject (motores)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Finding(Base):
    """Hallazgo del Scout (tendencia, herramienta o modelo nuevo). Una persona decide si se usa."""
    __tablename__ = "findings"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String, index=True)          # trend | tool
    title: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    why: Mapped[str] = mapped_column(Text, default="")             # por qué nos sirve
    how: Mapped[str] = mapped_column(Text, default="")             # cómo replicarlo / integrarlo
    ideas: Mapped[list] = mapped_column(JSON, default=list)        # ideas de posts listas para lanzar
    platforms: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default="new", index=True)  # new / used / dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Feedback(Base):
    """Decisiones humanas (aprobar, rechazar, elegir otro intento, pedir cambios, editar textos).
    Shelton las revisa y aprende de ellas."""
    __tablename__ = "feedback"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


def record_feedback(post_id: str, action: str, **detail) -> None:
    with session() as s:
        s.add(Feedback(post_id=post_id, action=action, detail=detail))
