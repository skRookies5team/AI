package com.meetingoneline.meeting_one_line.meeting;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.meetingoneline.meeting_one_line.global.exception.BusinessException;
import com.meetingoneline.meeting_one_line.global.exception.ErrorCode;
import com.meetingoneline.meeting_one_line.meeting.client.AiClient;
import com.meetingoneline.meeting_one_line.meeting.dto.MeetingRequestDto;
import com.meetingoneline.meeting_one_line.meeting.dto.MeetingResponseDto;
import com.meetingoneline.meeting_one_line.meeting.entity.MeetingEntity;
import com.meetingoneline.meeting_one_line.meeting.enums.RecordSaveStatus;
import com.meetingoneline.meeting_one_line.meeting.repository.MeetingRepository;
import com.meetingoneline.meeting_one_line.meeting.service.MeetingService;
import com.meetingoneline.meeting_one_line.user.UserEntity;
import com.meetingoneline.meeting_one_line.user.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.util.ReflectionTestUtils;

import java.io.IOException;
import java.time.LocalDateTime;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class MeetingServiceTest {

    @Mock
    private MeetingRepository meetingRepository;

    @Mock
    private UserRepository userRepository;

    @Mock
    private AiClient aiClient;

    @Mock
    private ObjectMapper objectMapper;

    @InjectMocks
    private MeetingService meetingService;

    private UserEntity mockUser;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        mockUser = UserEntity.create("user@test.com", "pw", "nickname");

        // UID 수동 주입
        ReflectionTestUtils.setField(mockUser, "id", UUID.randomUUID());

        // uploadDir 수동 세팅
        ReflectionTestUtils.setField(meetingService, "uploadDir", "./uploads/meetings");
    }

    /**
     * uploadMeeting 테스트
     */
    @Nested
    @DisplayName("회의록 업로드 테스트")
    class UploadMeeting {

        @Test
        @DisplayName("파일 업로드 성공 시 MeetingEntity 저장 및 AI 요청 수행")
        void uploadMeeting_success() throws IOException {
            // given
            UUID userId = UUID.randomUUID();
            MockMultipartFile mockFile =
                    new MockMultipartFile("file", "test.wav", "audio/wav", "dummy data".getBytes());

            MeetingRequestDto.CreateRequest request = new MeetingRequestDto.CreateRequest(
                    "회의 제목", LocalDateTime.now(), mockFile
            );

            when(userRepository.findById(userId)).thenReturn(Optional.of(mockUser));
            when(meetingRepository.save(any(MeetingEntity.class)))
                    .thenAnswer(invocation -> {
                        MeetingEntity m = invocation.getArgument(0);
                        ReflectionTestUtils.setField(m, "id", UUID.randomUUID());
                        return m;
                    });

            // when
            MeetingResponseDto.CreateResponse response = meetingService.uploadMeeting(userId, request);

            // then
            assertThat(response).isNotNull();
            assertThat(response.getStatus()).isEqualTo(RecordSaveStatus.UPLOADED);
            assertThat(response.getMessage()).contains("분석 요청 전송됨");

            verify(userRepository).findById(userId);
            verify(meetingRepository).save(any(MeetingEntity.class));
            verify(aiClient).requestAnalysis(eq(userId), any(UUID.class), anyString(), any());
        }

        @Test
        @DisplayName("파일이 없으면 INVALID_FILE 예외 발생")
        void uploadMeeting_fail_invalidFile() {
            // given
            UUID userId = UUID.randomUUID();
            MeetingRequestDto.CreateRequest request =
                    new MeetingRequestDto.CreateRequest("회의", LocalDateTime.now(), null);

            when(userRepository.findById(userId)).thenReturn(Optional.of(mockUser));

            // when & then
            assertThatThrownBy(() -> meetingService.uploadMeeting(userId, request))
                    .isInstanceOf(BusinessException.class)
                    .hasMessageContaining(ErrorCode.INVALID_FILE.getMessage());
        }
    }

    /**
     * processCallback 테스트
     */
    @Nested
    @DisplayName("processCallback() 테스트")
    class ProcessCallback {

        @Test
        @DisplayName("AI 콜백 수신 시 MeetingEntity 상태, 키워드, 화자 정보가 저장됨")
        void processCallback_success() {
            // given
            UUID meetingId = UUID.randomUUID();
            MeetingEntity meeting = MeetingEntity.create(mockUser, "테스트회의", LocalDateTime.now(), "path/file.wav");

            MeetingRequestDto.AiCallbackRequest request = MeetingRequestDto.AiCallbackRequest.builder()
                                                                                             .status("completed")
                                                                                             .summary("회의 요약입니다.")
                                                                                             .keywords(java.util.List.of("AI", "요약"))
                                                                                             .speakers(java.util.List.of(
                                                                                                     MeetingRequestDto.AiCallbackRequest.Speaker.builder()
                                                                                                                                                .speakerId("S1")
                                                                                                                                                .segments(java.util.List.of(
                                                                                                                                                        MeetingRequestDto.AiCallbackRequest.Segment.builder()
                                                                                                                                                                                                   .start(0.0f).end(1.5f).text("안녕하세요").build()
                                                                                                                                                ))
                                                                                                                                                .build()
                                                                                             ))
                                                                                             .build();

            when(meetingRepository.findById(meetingId)).thenReturn(Optional.of(meeting));
            when(meetingRepository.save(any(MeetingEntity.class))).thenReturn(meeting);

            // when
            MeetingResponseDto.AiCallbackResponse response = meetingService.processCallback(meetingId, request);

            // then
            assertThat(response.getMessage()).contains("성공적으로 저장되었습니다.");
            assertThat(meeting.getStatus()).isEqualTo(RecordSaveStatus.COMPLETED);
            assertThat(meeting.getKeywords()).hasSize(2);
            assertThat(meeting.getSpeakers()).hasSize(1);
        }
    }

    /**
     * updateMeeting 테스트
     */
    @Nested
    @DisplayName("updateMeeting() 테스트")
    class UpdateMeeting {

        @Test
        @DisplayName("회의 수정 시 제목, 요약, 키워드 변경 및 AI 업서트 호출")
        void updateMeeting_success() {
            // given
            UUID userId = UUID.randomUUID();
            UUID meetingId = UUID.randomUUID();

            MeetingEntity meeting = MeetingEntity.create(mockUser, "Old Title", LocalDateTime.now(), "file.wav");
            meeting.setSummary("Old Summary");

            when(userRepository.findById(userId)).thenReturn(Optional.of(mockUser));
            when(meetingRepository.findByIdAndDeletedAtIsNull(meetingId)).thenReturn(Optional.of(meeting));
            when(meetingRepository.save(any(MeetingEntity.class))).thenReturn(meeting);

            MeetingRequestDto.UpdateRequest request = MeetingRequestDto.UpdateRequest.builder()
                                                                                     .title("New Title")
                                                                                     .summary("New Summary")
                                                                                     .keywords(java.util.List.of("AI", "테스트"))
                                                                                     .build();

            // when
            MeetingResponseDto.CommonMessage response = meetingService.updateMeeting(userId, meetingId, request);

            // then
            assertThat(response.getMessage()).contains("회의록이 수정되었습니다.");
            assertThat(meeting.getTitle()).isEqualTo("New Title");
            assertThat(meeting.getSummary()).isEqualTo("New Summary");
            verify(aiClient).requestUpsertSync(eq(userId), eq(meetingId), anyString(), anyString(), anyList(), anyList());
        }
    }

    /**
     * deleteMeeting 테스트
     */
    @Nested
    @DisplayName("deleteMeeting() 테스트")
    class DeleteMeeting {

        @Test
        @DisplayName("회의 삭제 시 SoftDelete 처리 및 AI 서버 삭제 요청 수행")
        void deleteMeeting_success() {
            // given
            UUID userId = UUID.randomUUID();
            UUID meetingId = UUID.randomUUID();

            MeetingEntity meeting = MeetingEntity.create(mockUser, "삭제 테스트", LocalDateTime.now(), "dummy/path.wav");

            when(userRepository.findById(userId)).thenReturn(Optional.of(mockUser));
            when(meetingRepository.findById(meetingId)).thenReturn(Optional.of(meeting));

            // when
            MeetingResponseDto.CommonMessage response = meetingService.deleteMeeting(userId, meetingId);

            // then
            assertThat(response.getMessage()).contains("회의록이 삭제되었습니다.");
            assertThat(meeting.isDeleted()).isTrue();
            verify(aiClient).requestDeleteEmbeddingSync(userId, meetingId);
        }
    }
}

