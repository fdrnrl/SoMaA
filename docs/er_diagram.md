# ER-диаграмма схемы core

Сплошные линии — внешние ключи в базе (load_id → ops.load_log). Пунктирные — связи по смыслу:
в базе они не ограничены ключами (раздел 7.2) и проверяются в качестве данных (раздел 13).

```mermaid
erDiagram
    SESSIONS ||..o{ ANSWERS : "exam_type + participant_id"
    SESSIONS ||..o{ EXPERT_REVIEWS : "exam_type + participant_id"
    SESSIONS ||..o{ PROCTORING_EVENTS : "exam_type + participant_id"
    SESSIONS }o..|| SCHOOLS : "school_code"
    SESSIONS }o..|| REGIONS : "region_code"
    SESSIONS }o..|| SUBJECTS : "exam_type + subject_code"
    SCHOOLS }o..|| REGIONS : "region_code"
    SESSIONS }o..o{ KIM_TASKS : "exam_type + subject_code + variant_id"
    ANSWERS }o..o| KIM_TASKS : "task_number (через сессию)"
    EXPERT_REVIEWS }o..o| KIM_TASKS : "task_number (через сессию)"
    SCALES }o..|| SUBJECTS : "exam_type + subject_code"
    LOAD_LOG ||--o{ SESSIONS : "load_id"
    LOAD_LOG ||--o{ ANSWERS : "load_id"
    LOAD_LOG ||--o{ EXPERT_REVIEWS : "load_id"
    LOAD_LOG ||--o{ PROCTORING_EVENTS : "load_id"

    SESSIONS {
        text exam_type PK
        text record_id PK
        text participant_id
        text subject_code
        smallint grade_level
        text variant_id
        text region_code
        text school_code
        text room_code
        text status
        timestamptz started_at
        timestamptz finished_at
        smallint primary_score
        smallint max_primary_score
        text result_status
        jsonb exam_block
        timestamptz updated_at
        boolean deleted
    }
    ANSWERS {
        text exam_type PK
        text record_id PK
        text participant_id
        smallint task_number
        text answer
        text answer_norm
        boolean is_correct
        smallint score
        smallint max_score
        timestamptz answered_at
        integer time_spent_sec
        smallint audio_plays
    }
    EXPERT_REVIEWS {
        text exam_type PK
        text record_id PK
        text participant_id
        smallint task_number
        text check_status
        text check_stage
        text expert_code
        smallint score
        smallint max_score
        timestamptz reviewed_at
    }
    PROCTORING_EVENTS {
        text exam_type PK
        text record_id PK
        text participant_id
        text event_type
        timestamptz occurred_at
        smallint task_number
    }
    KIM_TASKS {
        text exam_type PK
        text record_id PK
        text subject_code
        smallint exam_year
        text variant_id
        smallint task_number
        smallint part
        text check_type
        smallint max_score
        text level
        text_array content_codes
        text_array skill_codes
        text section
        text block
    }
    SCALES {
        text exam_type PK
        text record_id PK
        text subject_code
        smallint exam_year
        text scale_type
        jsonb points
        jsonb conditions
        integer scale_version
        timestamptz valid_from
    }
    REGIONS {
        text region_code PK
        text name
    }
    SCHOOLS {
        text school_code PK
        text region_code
        text name
    }
    SUBJECTS {
        text exam_type PK
        text subject_code PK
        text name
        smallint grade_level
        smallint max_primary_score
        smallint threshold_score
    }
    LOAD_LOG {
        bigint id PK
        text platform
        text endpoint
        text status
    }
```

Во всех таблицах core с данными платформ также есть payload (jsonb), loaded_at и load_id;
у справочников и core.live_status — loaded_at и load_id.
