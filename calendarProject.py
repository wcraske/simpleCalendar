#to activate venv: source .venv/bin/activate
#to run project: uvicorn calendarProject:app --host 0.0.0.0 --port 8000 --reload
#docs: http://127.0.0.1:8000/docs
#uvicorn calendarProject:app --host 0.0.0.0 --port 8000 --reload
#if venv still active in shell but not really active: bash
from fastapi import FastAPI, Depends, HTTPException, status, Form, Query, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx
from sqlalchemy import Column, Integer, String, create_engine, DateTime, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship, joinedload
from sqlalchemy.future import select
from pydantic import BaseModel, Field
from typing import Annotated, List, Optional
from datetime import timedelta, timezone, datetime
import jwt
from jwt.exceptions import InvalidTokenError
from passlib.context import CryptContext
from fastapi import HTTPException


#constants
#key from running openssl rand -hex 32
SECRET_KEY = "edd740cac70ea072c01f42402ab1bb67e643fcab071d4298424b74e6cc4b3056"
ALGORITHM = "HS256" 
ACCESS_TOKEN_EXPIRE_MINUTES = 60
SQLALCHEMY_DATABASE_URL = "sqlite:///./calendarDB.db"
OPENWEATHER_API_KEY = "e31a1b552615cfa104798b72be288c19" 
#database setup
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

#template setup
templates = Jinja2Templates(directory="templates")


#models
class User(Base):
    __tablename__ = "users"
    #id for uniqueness
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String, unique=True, index=True)
    #hashed password for security
    hashed_password = Column(String)
    #role to determine if admin or regular user
    role = Column(String, default="user")
    #relationship to events
    events = relationship("Event", back_populates="owner", cascade="all, delete-orphan")

class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, index=True, unique=False)
    #optional description of event
    description = Column(String, index=True)
    start_date = Column(DateTime, index=True) 
    end_date = Column(DateTime, index=True)
    #relationship to user
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    owner = relationship("User", back_populates="events")



#schemas for pydantic
#for returning user data
class UserResponse(BaseModel):
    #id for user
    id: int  
    username: str 
    role: str  
    #configuration to allow ORM mode
    class Config:
        from_attributes = True  

#for returning event data
class EventCreate(BaseModel):
     #event name with validation
    name: str = Field(..., min_length=1, max_length=100) 
    description: str = Field(..., min_length=0, max_length=255) 
    start_date: datetime 
    end_date: datetime 

#for returning event data adding and id for specifying
class EventResponse(EventCreate):
    id: int 

    class Config:
        from_attributes = True 

#for returning user data for authentication
class UserInDB(BaseModel):
    username: str  
    hashed_password: str 
    role: str 

#for jwt token
class Token(BaseModel):
    access_token: str  
    token_type: str 

#for decoded jwt token
class TokenData(BaseModel):
    username: str | None = None  


#security
#hashing password with bcrypt and oauth2 setup for token
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

#gets plain password and hashes it
def get_password_hash(password: str):
    return pwd_context.hash(password)

#checks if plain password matches hashed version
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


#creating database and populating it with admin
def create_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        admin_user = db.query(User).filter(User.username == "admin").first()
        if not admin_user:
            hashed_password = get_password_hash("admin")
            admin_user = User(username="admin", hashed_password=hashed_password, role="admin")
            db.add(admin_user)
            db.commit()
            db.refresh(admin_user)
    finally:
        db.close()
create_db()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()



#helper functions for authentication
def weather_emoji(condition: str) -> str:
    mapping = {
        "Clear": "☀️",
        "Clouds": "☁️",
        "Rain": "🌧️",
        "Drizzle": "🌦️",
        "Thunderstorm": "⛈️",
        "Snow": "❄️",
        "Mist": "🌫️",
        "Fog": "🌁",
        "Smoke": "💨",
        "Haze": "🌁",
        "Dust": "🌪️",
        "Sand": "🏜️",
        "Ash": "🌋",
        "Squall": "🌬️",
        "Tornado": "🌪️"
    }
    return mapping.get(condition, "unknown weather condition")

#gets user from database
def get_user(db: Session, username: str):
    return db.query(User).filter(User.username == username).first()

#authenticates user with username and password
def authenticate_user(db, username: str, password: str):
    user = get_user(db, username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

#creates jwt token with a time limit 
def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

#for getting current user and validating based on jwt token
async def get_current_user(token: Annotated[str, Depends(oauth2_scheme)], db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
    except InvalidTokenError:
        raise credentials_exception
    user = get_user(db, username=token_data.username)
    if user is None:
        raise credentials_exception
    return user

#gets current user
async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)]):
    return current_user



#app initialization
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name = "static")



#routes
#route to get upcoming events for notifcation
@app.get("/upcoming-events/")
def get_upcoming_events(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Session = Depends(get_db),
    minutes: int = 30
):
    now = datetime.now()
    upcoming = now + timedelta(minutes=minutes)

    events = db.query(Event).filter(
        Event.user_id == current_user.id,
        Event.start_date >= now,
        Event.start_date <= upcoming
    ).all()
    
    return events

#route to get weather data from api
@app.get("/weather")
async def get_weather(city: str = "Vancouver"):
    try:
        async with httpx.AsyncClient() as client:
            url = (
                f"http://api.openweathermap.org/data/2.5/weather?"
                f"q={city}&appid={OPENWEATHER_API_KEY}&units=metric"
            )
            response = await client.get(url)
            
            if response.status_code != 200:
                data = response.json()
                raise HTTPException(
                    status_code=response.status_code,
                    detail=data.get("message", "failed to fetch weather data")
                )

            data = response.json()

            weather = {
                "city": data["name"],
                "temperature": data["main"]["temp"],
                "description": data["weather"][0]["description"],
                "emoji": weather_emoji(data["weather"][0]["main"])
            }

            return weather

    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"weather unavailable: {exc}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"error: {str(e)}"
        )

#route for getting token for login and getting token
@app.post("/token")
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db),
) -> Token:
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    return Token(access_token=access_token, token_type="bearer")


#route for getting current user after authetnication
@app.get("/users/me/", response_model=UserResponse)
async def read_users_me(
    current_user: Annotated[User, Depends(get_current_active_user)],
):
    return current_user 

#route for allowing new users to resgitser
@app.post("/register", response_model=Token)
def register(
    username: str = Form(..., min_length=3, max_length=16),
    password: str = Form(..., min_length=3, max_length=16),
    db: Session = Depends(get_db),
):    
    existing_user = db.query(User).filter(User.username == username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    hashed_password = get_password_hash(password)
    new_user = User(username=username, hashed_password=hashed_password, role="user")  # Default role: user
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    access_token = create_access_token(data={"sub": username})
    return {"access_token": access_token, "token_type": "bearer"}

#route for creating events
@app.post("/events/", response_model=EventResponse)
def create_event(
    current_user: Annotated[User, Depends(get_current_user)],
    event: EventCreate,
    db: Session = Depends(get_db),
    user_id: Optional[int] = None, 
):
    if current_user.role != "admin":
        user_id = current_user.id  
    elif user_id is None:
        raise HTTPException(status_code=400, detail="Admin must specify a user_id")

    db_user = db.query(User).options(joinedload(User.events)).filter(User.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    db_event = Event(**event.model_dump(), owner=db_user)
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    return db_event

#route for getting events with pagination
@app.get("/events/", response_model=List[EventResponse])
def get_events(
    current_user: Annotated[User, Depends(get_current_user)], 
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(10, le=100),
    search: Optional[str] = None,
):
    query = db.query(Event)
    if current_user.role != "admin":  
        query = query.filter(Event.owner == current_user)

    if search:
        query = query.filter(Event.name.contains(search) | Event.description.contains(search))

    return query.offset(skip).limit(limit).all()

#route for getting specific event
@app.get("/events/{event_id}", response_model=EventResponse)
def get_event(event_id: int, db: Session = Depends(get_db)):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event

#route for updating events
@app.put("/events/{event_id}", response_model=EventResponse)
def update_event(
    current_user: Annotated[User, Depends(get_current_user)],
    event_id: int,
    updated_event: EventCreate,
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if current_user.role != "admin" and event.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You do not have permission to update this event")

    for key, value in updated_event.model_dump().items():
        setattr(event, key, value)
    db.commit()
    db.refresh(event)
    return event

#route for deleting events
@app.delete("/events/{event_id}")
def delete_event(
    current_user: Annotated[User, Depends(get_current_user)],
    event_id: int,
    db: Session = Depends(get_db),
):
    event = db.query(Event).options(joinedload(Event.owner)).filter(Event.id == event_id).first()
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    if current_user.role != "admin" and event.owner != current_user:
        raise HTTPException(status_code=403, detail="You do not have permission to delete this event")

    db.delete(event)
    db.commit()
    return {"message": "Event deleted successfully"}

#route for serving main page
@app.get("/", response_class=HTMLResponse)
def main_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

#route for serving webpage for login
@app.get("/auth", response_class=HTMLResponse)
def auth_page(request: Request):
    return templates.TemplateResponse("auth.html", {"request": request})

#route for serving webpage for client
@app.get("/client", response_class=HTMLResponse)
def client_page(request: Request):
    return templates.TemplateResponse("client.html", {"request": request})

