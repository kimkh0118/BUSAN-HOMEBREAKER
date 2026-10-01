import requests

# 주의: 공공데이터포털 마이페이지에 있는 '일반 인증키 (Encoding)' 값을 그대로 붙여넣으세요.
# (보통 끝이 %3D%3D 로 끝납니다. 예: abcDEF123%2B...%3D%3D)
DATA_KEY = "93506e60dafcf9ec5a7ed567ad4a585aa03e8c70c05a6f124a057984c9e5c729"

# 광안동 734-8 (힐튼파크) 주소 하드코딩 테스트
url = (
    "http://apis.data.go.kr/1613000/BldRgstService_v2/getBrTitleInfo?"
    f"serviceKey={DATA_KEY}&"
    "sigunguCd=26500&"
    "bjdongCd=10400&"
    "platGbCd=0&"
    "bun=0734&"
    "ji=0008&"
    "numOfRows=10&"
    "pageNo=1&"
    "_type=json"
)

print("요청 URL:", url)
print("-" * 50)

try:
    response = requests.get(url, timeout=10)
    print("응답 상태 코드:", response.status_code)
    print("응답 데이터:", response.text)
except Exception as e:
    print("요청 중 에러 발생:", e)