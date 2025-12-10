# 🐛 백엔드 이슈: 회의록 제목이 userId로 표시되는 문제

## 📋 문제 설명

회의록 생성 시 프론트엔드에서 전달한 `title` 파라미터가 무시되고, **userId와 파일명이 결합된 형태**로 제목이 저장되는 문제가 발생하고 있습니다.

### 현재 상황
- **기대하는 제목**: "testt1" (사용자가 입력한 제목)
- **실제 표시 제목**: "8892f1c6-968b-4b5b-a57b-a8699cc720a9_testt1"

이는 userId + underscore + 파일명 조합으로 보이며, 사용자가 입력한 제목이 반영되지 않고 있습니다.

---

## 🔍 프론트엔드 확인 사항

### 1. 프론트엔드는 제목을 정확히 전달하고 있음

프론트엔드에서는 `FormData`를 통해 다음과 같이 전달합니다:

```javascript
const formData = new FormData()
formData.append('title', 'testt1')  // 사용자가 입력한 제목
formData.append('date', '2025-01-15T10:00:00Z')
formData.append('file', audioFile)
```

### 2. API 엔드포인트

```
POST http://localhost:8080/api/meetings
Content-Type: multipart/form-data
Authorization: Bearer {accessToken}

FormData:
- title: "testt1"
- date: "2025-01-15T10:00:00Z"
- file: (audio file)
```

### 3. 디버깅 로그 추가됨

이제 파일 업로드 시 브라우저 콘솔에 다음과 같은 로그가 표시됩니다:

```
📤 [프론트엔드] 업로드 요청 데이터:
  - title: testt1
  - date: 2025-01-15T10:00:00Z
  - file: audio.webm audio/webm 2048576

📥 [백엔드] 응답 데이터:
  {
    meetingId: "meeting_123",
    status: "uploaded",
    message: "파일 업로드 완료"
  }
```

**다음 업로드 시 이 로그를 확인하여 백엔드 개발자에게 전달해주세요.**

---

## 🛠️ 백엔드에서 확인해야 할 사항

### 1. FormData 파라미터 수신 확인

백엔드 컨트롤러에서 `title` 파라미터를 제대로 받고 있는지 확인:

```java
@PostMapping("/meetings")
public ResponseEntity<?> createMeeting(
    @RequestParam("title") String title,  // ⚠️ 이 값이 제대로 들어오는지 확인
    @RequestParam("date") String date,
    @RequestParam("file") MultipartFile file
) {
    // title 값 로그 출력
    log.info("📥 [백엔드] 받은 title: {}", title);
    log.info("📥 [백엔드] 받은 파일명: {}", file.getOriginalFilename());
    
    // ...
}
```

### 2. DB 저장 시 title 필드 확인

Meeting 엔티티 저장 시 title이 올바르게 설정되는지 확인:

```java
Meeting meeting = Meeting.builder()
    .title(title)  // ⚠️ 여기에 userId나 파일명이 들어가고 있는지 확인
    .date(date)
    .status("uploaded")
    .build();

// 저장 전 로그
log.info("💾 [백엔드] 저장할 Meeting 객체: {}", meeting);

meetingRepository.save(meeting);
```

### 3. 파일명과 title을 혼동하고 있는지 확인

혹시 다음과 같은 코드가 있는지 확인:

```java
// ❌ 잘못된 예시 1: 파일명을 title로 사용
meeting.setTitle(file.getOriginalFilename());

// ❌ 잘못된 예시 2: userId와 파일명 조합
meeting.setTitle(userId + "_" + file.getOriginalFilename());

// ✅ 올바른 예시: 요청 파라미터의 title 사용
meeting.setTitle(title);
```

### 4. AOP나 Interceptor에서 덮어쓰고 있는지 확인

AOP나 Interceptor에서 title을 자동으로 변경하고 있을 가능성:

```java
// 혹시 이런 코드가 있는지 확인
@Before("execution(* com.example.meeting.service.*.*(..))")
public void beforeMethod(JoinPoint joinPoint) {
    // title을 자동으로 변경하는 로직이 있는지 확인
}
```

---

## 🎯 해결 방법

### 백엔드 수정 예시 (Spring Boot)

```java
@PostMapping("/meetings")
public ResponseEntity<?> createMeeting(
    @RequestParam("title") String title,
    @RequestParam("date") String date,
    @RequestParam("file") MultipartFile file,
    @AuthenticationPrincipal UserDetails userDetails
) {
    // 디버깅 로그
    log.info("📥 받은 title: {}", title);
    log.info("📥 받은 파일명: {}", file.getOriginalFilename());
    log.info("📥 사용자: {}", userDetails.getUsername());
    
    // 파일 저장 (파일명은 내부적으로만 사용)
    String savedFileName = userId + "_" + file.getOriginalFilename();
    String filePath = fileStorageService.save(file, savedFileName);
    
    // Meeting 객체 생성 (title은 사용자 입력값 사용)
    Meeting meeting = Meeting.builder()
        .title(title)  // ✅ 사용자가 입력한 제목 사용
        .date(LocalDateTime.parse(date))
        .filePath(filePath)  // 파일 경로는 별도 필드
        .status("uploaded")
        .userId(userId)
        .build();
    
    Meeting savedMeeting = meetingRepository.save(meeting);
    
    log.info("💾 저장된 Meeting: {}", savedMeeting);
    
    return ResponseEntity.ok(CreateMeetingResponse.builder()
        .meetingId(savedMeeting.getMeetingId())
        .status(savedMeeting.getStatus())
        .message("파일 업로드 완료")
        .build());
}
```

---

## ✅ 체크리스트

백엔드 개발자가 확인해야 할 항목:

- [ ] `@RequestParam("title")` 파라미터가 제대로 수신되는지 확인
- [ ] 수신된 `title` 값을 로그로 출력해서 확인
- [ ] Meeting 엔티티에 저장되는 `title` 값 확인
- [ ] `file.getOriginalFilename()`을 `title`로 사용하고 있는지 확인
- [ ] AOP/Interceptor에서 `title`을 자동 변경하는 로직 확인
- [ ] DB에서 실제 저장된 `title` 컬럼 값 확인

---

## 🧪 테스트 방법

### 1. 프론트엔드에서 테스트
1. 회의록 업로드 시 제목 입력: "테스트 회의"
2. 브라우저 콘솔(F12) 확인
3. 백엔드 로그 확인
4. 회의록 목록에서 제목 확인

### 2. Postman으로 직접 테스트

```
POST http://localhost:8080/api/meetings
Authorization: Bearer {accessToken}
Content-Type: multipart/form-data

Body (form-data):
- title: "테스트 회의"
- date: "2025-01-15T10:00:00Z"
- file: (audio file)
```

응답 확인:
```json
{
  "meetingId": "meeting_123",
  "status": "uploaded",
  "message": "파일 업로드 완료"
}
```

그 다음 GET `/api/meetings` 호출해서 실제 저장된 title 확인:
```json
{
  "content": [
    {
      "meetingId": "meeting_123",
      "title": "테스트 회의",  // ⚠️ 이 값이 올바른지 확인
      ...
    }
  ]
}
```

---

## 📞 연락처

이 문서를 백엔드 개발자에게 전달하고, 위의 로그와 함께 문제를 공유해주세요.

**다음 회의록 업로드 시 브라우저 콘솔(F12)을 열고 로그를 캡처해서 전달하면 더 빠르게 해결할 수 있습니다!** 📸

