# Контуры регионов

`regions.geojson.gz` — настоящие Polygon/MultiPolygon, WGS84; 83 объекта исходного
набора, дата записей 2013-12-04. Контуры не являются актуальным официальным справочником
границ. Для использования на рабочем стенде команда должна согласовать набор и обновление.

Источник: [Click That 'Hood](https://github.com/codeforgermany/click_that_hood/blob/main/public/data/russia.geojson),
[метаданные](https://github.com/codeforgermany/click_that_hood/blob/main/public/data/russia.metadata.json)
указывают [GIS-Lab/OpenStreetMap](https://gis-lab.info/qa/osm-adm.html).
© OpenStreetMap contributors; [ODbL и атрибуция](https://www.openstreetmap.org/copyright).
Лицензия MIT исходного репозитория относится к его программному обеспечению;
она здесь не подменяет условия использования данных OpenStreetMap.

SHA-256 исходного GeoJSON:
`c55b928c4ec0637d3c485e970c3d1f319244cbaa23694a676f09eb78284d92d2`.
При упаковке сохранены координаты; свойства сокращены до имени и region_code.
20 кодов сопоставлены с REGIONS из sma/generator/population.py, включая
02 = Башкортостан, 16 = Татарстан, 77 = Москва. Остальные объекты служат серым
географическим фоном и не получают выдуманных кодов. Несопоставленные коды данных
показываются предупреждением и остаются в таблице результатов.
