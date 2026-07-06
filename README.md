지방세무 카카오톡 FAQ 챗봇
GPT-4o mini는 "질문 의도 분류"만 하고, 실제 답변은 항상 faq.json에 등록된
원문을 그대로 돌려주는 지방세 안내 챗봇입니다.
파일 구성
파일
역할
main.py
서버의 핵심 코드. 카카오 웹훅, FAQ 관리, 로그 조회 기능이 모두 여기 들어있습니다.
faq.json
질문-답변 데이터베이스. 여기 있는 답변만 사용자에게 그대로 전달됩니다.
contacts.json
세목별(재산세-토지, 자동차세 등) 담당 부서 연락처.
requirements.txt
이 프로젝트 실행에 필요한 파이썬 부품 목록.
.env.example
환경변수 작성 예시. 복사해서 .env로 저장 후 실제 값을 채우세요.
.gitignore
GitHub에 올리면 안 되는 파일(.env, logs/)을 제외하는 목록.
logs/unanswered.csv
AI가 답을 찾지 못한 질문들이 자동으로 쌓이는 파일 (실행 후 생성됨).
로컬에서 실행하는 법
pip install -r requirements.txt
uvicorn main:app --reload
Bash
실행 후 브라우저에서 http://127.0.0.1:8000/docs 접속 → Swagger UI에서
모든 기능을 클릭만으로 테스트할 수 있습니다.
주요 API
POST /kakao/webhook : 카카오 i 오픈빌더 스킬에 등록할 주소
GET /faq : 등록된 FAQ 전체 조회
POST /faq : FAQ 추가 (헤더에 X-Admin-Key 필요)
PUT /faq/{faq_id} : FAQ 수정 (헤더에 X-Admin-Key 필요)
DELETE /faq/{faq_id} : FAQ 삭제 (헤더에 X-Admin-Key 필요)
GET /logs/unanswered : 미답변 질문 목록 (헤더에 X-Admin-Key 필요)
GET /logs/unanswered/csv : 미답변 질문 CSV 다운로드 (헤더에 X-Admin-Key 필요)
GET /logs/unanswered/summary : 미답변 질문 빈도순 요약 (헤더에 X-Admin-Key 필요)
X-Admin-Key는 .env에 적어둔 ADMIN_API_KEY 값과 같아야 통과됩니다.
