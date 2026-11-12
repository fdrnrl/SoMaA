"""Рабочие страницы дашборда и их маршруты."""

from .analysis import analysis_page
from .anomalies import anomalies_page
from .comparison import comparison_page
from .distribution import distribution_page
from .grades import grades_page
from .live import live_page
from .regions import regions_page
from .topics import topics_page

WORK_PAGES = (
    ("Распределение", "distribution", distribution_page),
    ("Сравнение экзаменов", "comparison", comparison_page),
    ("Отметки", "grades", grades_page),
    ("Регионы", "regions", regions_page),
    ("Темы и варианты", "topics", topics_page),
    ("Живой экран", "live", live_page),
    ("Аномалии", "anomalies", anomalies_page),
    ("Углубленный анализ", "analysis", analysis_page),
)
