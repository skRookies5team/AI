package com.meetingoneline.meeting_one_line.meeting.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.meetingoneline.meeting_one_line.feedback.entity.*;
import com.meetingoneline.meeting_one_line.feedback.repository.FeedbackRepository;
import com.meetingoneline.meeting_one_line.global.exception.BusinessException;
import com.meetingoneline.meeting_one_line.global.exception.ErrorCode;
import com.meetingoneline.meeting_one_line.meeting.client.AiClient;
import com.meetingoneline.meeting_one_line.meeting.dto.MeetingRequestDto;
import com.meetingoneline.meeting_one_line.meeting.dto.MeetingResponseDto;
import com.meetingoneline.meeting_one_line.meeting.entity.KeywordEntity;
import com.meetingoneline.meeting_one_line.meeting.entity.MeetingEntity;
import com.meetingoneline.meeting_one_line.meeting.entity.SegmentEntity;
import com.meetingoneline.meeting_one_line.meeting.entity.SpeakerEntity;
import com.meetingoneline.meeting_one_line.meeting.enums.RecordSaveStatus;
import com.meetingoneline.meeting_one_line.meeting.repository.MeetingRepository;
import com.meetingoneline.meeting_one_line.meeting.repository.MeetingSpecification;
import com.meetingoneline.meeting_one_line.user.UserEntity;
import com.meetingoneline.meeting_one_line.user.UserRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.time.LocalDateTime;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.UUID;

@Slf4j
@Service
@RequiredArgsConstructor
@Transactional
public class MeetingService {

    private final MeetingRepository meetingRepository;
    private final UserRepository userRepository;
    private final FeedbackRepository feedbackRepository;
    private final AiClient aiClient;
    private final ObjectMapper objectMapper;

    @Value("${file.upload-dir:./uploads/meetings}")
    private String uploadDir;

    @Transactional
    public MeetingResponseDto.CreateResponse uploadMeeting(UUID userId, MeetingRequestDto.CreateRequest request) {
        UserEntity user = userRepository.findById(userId)
                                        .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));

        MultipartFile file = request.getFile();
        if (file == null || file.isEmpty()) {
            throw new BusinessException(ErrorCode.INVALID_FILE);
        }

        try {
            // 1. 경로 폴더 없으면 추가
            File baseDir = new File(uploadDir).getAbsoluteFile();
            if (!baseDir.exists()) {
                boolean created = baseDir.mkdirs();
                log.info("📁 기본 업로드 폴더 생성됨: {} (성공여부: {})", baseDir, created);
            }

            // 2. 유저별 폴더
            File userDir = new File(baseDir, userId.toString());
            if (!userDir.exists()) {
                boolean created = userDir.mkdirs();
                log.info("사용자 업로드 폴더 생성됨: {} (성공여부: {})", userDir, created);
            }

            // 3. 파일명 생성
            String fileName = UUID.randomUUID() + "_" + file.getOriginalFilename();
            File destination = new File(userDir, fileName);

            log.info("💾 파일 저장 시도: {}", destination.getAbsolutePath());
            file.transferTo(destination);

            // 4.DB 저장
            MeetingEntity meeting = MeetingEntity.create(user, request.getTitle(), request.getDate(), destination.getAbsolutePath());
            MeetingEntity saved = meetingRepository.save(meeting);

            // 5. AI 서버 분석 요청 시도
            try {
                aiClient.requestAnalysis(
                        userId,
                        meeting.getId(),
                        destination.getAbsolutePath(),
                        saved.getTitle(),
                        error -> updateMeetingStatus(meeting.getId(), RecordSaveStatus.FAILED, "AI 서버 요청 실패")
                );
                meeting.updateStatusAndSummary(RecordSaveStatus.PROCESSING.name(), null);
            } catch (Exception e) {
                log.error("❌ AI 서버 요청 실패 - meetingId={}", meeting.getId(), e);
                meeting.updateStatusAndSummary(RecordSaveStatus.FAILED.name(), "AI 서버 분석 요청 실패");
            }


            return MeetingResponseDto.CreateResponse.builder()
                                                   .meetingId(UUID.fromString(saved.getId().toString()))
                                                   .status(RecordSaveStatus.UPLOADED)
                                                   .message("파일 업로드 완료 및 분석 요청 전송됨")
                                                   .build();

        } catch (IOException e) {
            log.error("파일 저장 중 오류 발생", e);
            throw new BusinessException(ErrorCode.FILE_UPLOAD_FAILED);
        }
    }

    /**
     * 회의 분석 callback
     */
    @Transactional
    public MeetingResponseDto.AiCallbackResponse processCallback(UUID meetingId, MeetingRequestDto.AiCallbackRequest request) {
        MeetingEntity meeting = meetingRepository.findById(meetingId)
                                                 .orElseThrow(() -> new BusinessException(ErrorCode.MEETING_NOT_FOUND));


        log.info("AI 콜백 수신: meetingId={}, status={}, summary={}", meetingId, request.getStatus(), request.getSummary());

        // 1. 회의 상태 및 요약문 업데이트
        meeting.updateStatusAndSummary(request.getStatus(), request.getSummary());

        // 2. 기존 데이터 초기화
        meeting.getSpeakers().clear();
        meeting.getKeywords().clear();

        // 3. 키워드 갱신
        if (request.getKeywords() != null) {
            for (String keyword : request.getKeywords()) {
                meeting.getKeywords().add(KeywordEntity.create(meeting, keyword));
            }
        }

        // 4. 화자 및 세그먼트 갱신
        if (request.getSpeakers() != null) {
            for (MeetingRequestDto.AiCallbackRequest.Speaker speakerReq : request.getSpeakers()) {
                SpeakerEntity speaker = SpeakerEntity.create(meeting, speakerReq.getSpeakerId(), null);

                if (speakerReq.getSegments() != null) {
                    for (MeetingRequestDto.AiCallbackRequest.Segment seg : speakerReq.getSegments()) {
                        speaker.getSegments().add(
                                SegmentEntity.create(speaker, seg.getStart(), seg.getEnd(), seg.getText())
                        );
                    }
                }

                meeting.getSpeakers().add(speaker);
            }
        }

        // 5. 피드백 데이터 생성 또는 갱신
        if (request.getFeedback() != null) {
            var feedbackDto = request.getFeedback();

            FeedbackEntity feedback = meeting.getId() != null
                    ? feedbackRepository.findByMeetingId(meeting.getId()).orElse(null)
                    : null;

            if (feedback == null) {
                feedback = FeedbackEntity.create(meeting);
            } else {
                feedback.getActionItems().clear();
                feedback.getTopics().clear();
                feedback.getFollowUpCategories().clear();
            }

            // Action Items
            if (feedbackDto.getActionItems() != null) {
                for (var ai : feedbackDto.getActionItems()) {
                    feedback.getActionItems().add(
                            ActionItemEntity.create(feedback, ai.getName(), ai.getContent(), ai.getOrderIndex())
                    );
                }
            }

            // Topics
            if (feedbackDto.getTopics() != null) {
                for (var topic : feedbackDto.getTopics()) {
                    feedback.getTopics().add(
                            TopicEntity.create(feedback, topic.getTitle(), topic.getImportance(), topic.getSummary(), topic.getProportion())
                    );
                }
            }

            // Follow-up Categories & Questions
            if (feedbackDto.getFollowUpCategories() != null) {
                for (var cat : feedbackDto.getFollowUpCategories()) {
                    FollowUpCategoryEntity categoryEntity = FollowUpCategoryEntity.create(feedback, cat.getCategory());

                    if (cat.getQuestions() != null) {
                        for (var q : cat.getQuestions()) {
                            categoryEntity.getQuestions().add(
                                    FollowUpQuestionEntity.create(categoryEntity, q.getQuestion(), q.getOrderIndex())
                            );
                        }
                    }

                    feedback.getFollowUpCategories().add(categoryEntity);
                }
            }

            feedbackRepository.save(feedback);

            log.info("🧠 회의({})의 피드백 데이터 갱신 완료", meetingId);
        }

        meetingRepository.save(meeting);
        log.info("✅ 회의({}) 분석 및 피드백 저장 완료", meetingId);

        return MeetingResponseDto.AiCallbackResponse.builder()
                                                    .message("회의록 및 피드백 결과가 성공적으로 저장되었습니다.")
                                                    .build();
    }


    public MeetingResponseDto.ListResponse getMeetings(
            UUID userId,
            int page,
            int size,
            String keyword,
            String title,
            String summary,
            String status
    ) {
        try {
            String responseBody = aiClient.requestSearch(userId, page, size, keyword, title, summary, status)
                                          .block();

            log.info("AI 서버 응답 수신 완료: {}", responseBody);

            // FastAPI에서 내려주는 JSON 구조를 DTO로 역직렬화
            return objectMapper.readValue(responseBody, MeetingResponseDto.ListResponse.class);

        } catch (Exception e) {
            log.error("AI 서버 회의록 목록 조회 중 오류 발생", e);
            throw new RuntimeException("AI 서버 회의록 목록 조회 실패", e);
        }
    }

    /**
     * 회의 목록 조건 조회
     * @param keyword 회의 키워드
     * @param summary 회의 내용
     * @param status 회의 분석 상태
     * @return
     */
    @Transactional(readOnly = true)
    public MeetingResponseDto.ListResponse getMeetings2(
            UUID userId,
            int page,
            int size,
            String keyword,
            String title,
            String summary,
            String status
    ) {
        UserEntity user = userRepository.findById(userId)
                                        .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));

        Pageable pageable = PageRequest.of(page - 1, size, Sort.by(Sort.Direction.DESC, "createdAt"));

        // JPA Specification 사용 (동적 쿼리)
        Specification<MeetingEntity> spec = null;

        // 기본 필터: 유저 조건 추가
        spec = and(spec, MeetingSpecification.byUser(user));

        // keyword / title / summary / status 조건 추가
        spec = and(spec, keyword != null && !keyword.isBlank() ? MeetingSpecification.titleOrSummaryContains(keyword) : null);
        spec = and(spec, title != null && !title.isBlank() ? MeetingSpecification.titleContains(title) : null);
        spec = and(spec, summary != null && !summary.isBlank() ? MeetingSpecification.summaryContains(summary) : null);

        if (status != null && !status.isBlank()) {
            try {
                RecordSaveStatus recordStatus = RecordSaveStatus.valueOf(status.toUpperCase());
                spec = and(spec, MeetingSpecification.hasStatus(recordStatus));
            } catch (IllegalArgumentException ignored) {
                log.warn("⚠️ 잘못된 status 파라미터: {}", status);
            }
        }

        Page<MeetingEntity> result = meetingRepository.findAll(spec, pageable);

        return MeetingResponseDto.ListResponse.from(result);
    }

    private <T> Specification<T> and(Specification<T> base, Specification<T> next) {
        if (base == null) return next;
        if (next == null) return base;
        return base.and(next);
    }

    /**
     * 회의 상세 조회
     */
    @Transactional(readOnly = true)
    public MeetingResponseDto.DetailResponse getMeetingDetail(UUID userId, UUID meetingId) {
        // 1. 유저 및 회의 검증
        UserEntity user = userRepository.findById(userId)
                                        .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));

        MeetingEntity meeting = meetingRepository.findById(meetingId)
                                                 .orElseThrow(() -> new BusinessException(ErrorCode.MEETING_NOT_FOUND));

        // 본인 회의가 아니면 접근 불가
        if (!meeting.getUser().getId().equals(user.getId())) {
            throw new BusinessException(ErrorCode.ACCESS_DENIED);
        }

        // 2. 키워드 및 화자 데이터 변환
        var keywords = meeting.getKeywords().stream()
                              .map(KeywordEntity::getKeyword)
                              .toList();

        var speakers = meeting.getSpeakers().stream()
                              .map(speaker -> MeetingResponseDto.DetailResponse.Speaker.builder()
                                                                                       .speakerId(speaker.getSpeakerId())
                                                                                       .name(speaker.getName())
                                                                                       .segments(speaker.getSegments().stream()
                                                                                                        .sorted(Comparator.comparing(SegmentEntity::getStartTime))
                                                                                       .map(seg -> MeetingResponseDto.DetailResponse.Segment.builder()
                                                                                                                                                             .start(seg.getStartTime())
                                                                                                                                                             .end(seg.getEndTime())
                                                                                                                                                             .text(seg.getText())
                                                                                                                                                             .build())
                                                                                                        .toList())
                                                                                       .build())
                              .toList();

        // 3. 응답 구성
        return MeetingResponseDto.DetailResponse.builder()
                                                .meetingId(meeting.getId())
                                                .title(meeting.getTitle())
                                                .date(meeting.getDate())
                                                .status(meeting.getStatus().name().toLowerCase())
                                                .summary(meeting.getSummary())
                                                .keywords(keywords)
                                                .speakers(speakers)
                                                .filePath(meeting.getFilePath())
                                                .build();
    }

    @Transactional
    public MeetingResponseDto.CommonMessage updateMeeting(UUID userId, UUID meetingId, MeetingRequestDto.UpdateRequest request) {
        UserEntity user = userRepository.findById(userId)
                                        .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));

        MeetingEntity meeting = meetingRepository.findByIdAndDeletedAtIsNull(meetingId)
                                                 .orElseThrow(() -> new BusinessException(ErrorCode.MEETING_NOT_FOUND));

        if (!meeting.getUser().getId().equals(user.getId())) {
            throw new BusinessException(ErrorCode.ACCESS_DENIED);
        }

        // 제목 수정
        if (request.getTitle() != null && !request.getTitle().isBlank()) {
            meeting.setTitle(request.getTitle());
        }

        // 요약문 수정
        if (request.getSummary() != null && !request.getSummary().isBlank()) {
            meeting.updateStatusAndSummary(meeting.getStatus().name(), request.getSummary());
        }

        // 키워드 수정
        if (request.getKeywords() != null) {
            meeting.getKeywords().clear();
            request.getKeywords().forEach(keyword ->
                    meeting.getKeywords().add(KeywordEntity.create(meeting, keyword))
            );
        }

        // 화자 및 세그먼트 수정
        if (request.getSpeakers() != null) {
            for (MeetingRequestDto.UpdateRequest.SpeakerUpdate speakerReq : request.getSpeakers()) {
                meeting.getSpeakers().stream()
                       .filter(s -> s.getSpeakerId().equals(speakerReq.getSpeakerId()))
                       .findFirst()
                       .ifPresent(speaker -> {
                           // 이름 수정
                           if (speakerReq.getName() != null && !speakerReq.getName().isBlank()) {
                               speaker.setName(speakerReq.getName());
                           }

                           // 세그먼트 수정 (기존 전체 삭제 후 새로 추가)
                           if (speakerReq.getSegments() != null) {
                               speaker.getSegments().clear();
                               for (MeetingRequestDto.UpdateRequest.SpeakerUpdate.SegmentUpdate segReq : speakerReq.getSegments()) {
                                   SegmentEntity newSegment = SegmentEntity.create(
                                           speaker,
                                           segReq.getStart(),
                                           segReq.getEnd(),
                                           segReq.getText()
                                   );
                                   speaker.getSegments().add(newSegment);
                               }
                           }
                       });
            }
        }

        meetingRepository.save(meeting);

        // AI 서버 요청
        try {
            var speakers = meeting.getSpeakers().stream()
                                  .map(sp -> Map.ofEntries(
                                          Map.entry("speakerId", sp.getSpeakerId()),
                                          Map.entry("name", sp.getName() != null ? sp.getName() : ""),
                                          Map.entry("segments", sp.getSegments().stream()
                                                                  .map(seg -> Map.ofEntries(
                                                                          Map.entry("start", seg.getStartTime() != null ? seg.getStartTime() : 0.0),
                                                                          Map.entry("end", seg.getEndTime() != null ? seg.getEndTime() : 0.0),
                                                                          Map.entry("text", seg.getText() != null ? seg.getText() : "")
                                                                  ))
                                                                  .toList())
                                  )).toList();


            aiClient.requestUpsertSync(
                    userId,
                    meetingId,
                    meeting.getTitle(),
                    meeting.getSummary(),
                    meeting.getKeywords().stream().map(KeywordEntity::getKeyword).toList(),
                    speakers
            );

        } catch (BusinessException e) {
            log.error("❌ AI 서버 업서트 실패 - 트랜잭션 롤백됨 (meetingId={})", meetingId);
            throw e; // ai 서버 요청 실패시 -> rollback
        }

        return MeetingResponseDto.CommonMessage.builder()
                                               .message("회의록이 수정되었습니다.")
                                               .build();
    }

    @Transactional
    public MeetingResponseDto.CommonMessage deleteMeeting(UUID userId, UUID meetingId) {
        UserEntity user = userRepository.findById(userId)
                                        .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));

        MeetingEntity meeting = meetingRepository.findById(meetingId)
                                                 .orElseThrow(() -> new BusinessException(ErrorCode.MEETING_NOT_FOUND));

        if (!meeting.getUser().getId().equals(user.getId())) {
            throw new BusinessException(ErrorCode.ACCESS_DENIED);
        }

        // Soft Delete 처리
        meeting.delete();

        // 로컬 파일 삭제 시도 (선택)
        File file = new File(meeting.getFilePath());
        if (file.exists()) {
            boolean deleted = file.delete();
            log.info("🗑 파일 삭제됨: {} (성공여부: {})", file.getAbsolutePath(), deleted);
        }

        // AI 서버 임베딩 삭제 요청
        try {
            aiClient.requestDeleteEmbeddingSync(userId, meetingId);
        } catch (BusinessException e) {
            log.error("❌ AI 서버 삭제 요청 실패 - 트랜잭션 롤백됨 (meetingId={})", meetingId);
            throw e;
        }

        meetingRepository.save(meeting);

        return MeetingResponseDto.CommonMessage.builder()
                                               .message("회의록이 삭제되었습니다.")
                                               .build();
    }

    /**
     * AI 요청 실패 등으로 상태 변경이 필요한 경우 호출
     */
    @Transactional
    public void updateMeetingStatus(UUID meetingId, RecordSaveStatus status, String summary) {
        meetingRepository.findById(meetingId).ifPresent(meeting -> {
            meeting.updateStatusAndSummary(status.name(), summary);
            meetingRepository.save(meeting);
            log.warn("### 회의({}) 상태 변경됨 → {}", meetingId, status);
        });
    }

    @Transactional(readOnly = true)
    public MeetingResponseDto.CheckAnalysisCompleteResponse checkAnalysisComplete(UUID userId) {
        UserEntity user = userRepository.findById(userId)
                                        .orElseThrow(() -> new BusinessException(ErrorCode.USER_NOT_FOUND));

        List<RecordSaveStatus> excludedStatuses = List.of(
                RecordSaveStatus.COMPLETED,
                RecordSaveStatus.FAILED
        );

        LocalDateTime cutoff = LocalDateTime.now().minusHours(1);

        List<MeetingEntity> recentMeetings = meetingRepository.findRecentIncompleteMeetings(user, excludedStatuses, cutoff);

        List<MeetingResponseDto.CheckAnalysisCompleteResponse.CheckAnalysisComplete> list =
                recentMeetings.stream()
                              .map(meeting -> MeetingResponseDto.CheckAnalysisCompleteResponse.CheckAnalysisComplete.builder()
                                                                                                                        .meetingId(meeting.getId())
                                                                                                                        .title(meeting.getTitle())
                                                                                                                        .status(meeting.getStatus())
                                                                                                                        .createdAt(meeting.getCreatedAt())
                                                                                                                        .build())
                              .toList();

        return MeetingResponseDto.CheckAnalysisCompleteResponse.builder()
                                                               .completedMeetings(list)
                                                               .build();
    }


}
