package com.meetingoneline.meeting_one_line.feedback.service;

import com.meetingoneline.meeting_one_line.feedback.dto.FeedbackResponseDto;
import com.meetingoneline.meeting_one_line.feedback.entity.*;
import com.meetingoneline.meeting_one_line.feedback.repository.FeedbackRepository;
import com.meetingoneline.meeting_one_line.global.exception.BusinessException;
import com.meetingoneline.meeting_one_line.global.exception.ErrorCode;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.stream.Collectors;
import java.util.UUID;

@Service
@RequiredArgsConstructor
@Transactional(readOnly = true)
public class FeedbackService {

    private final FeedbackRepository feedbackRepository;

    public FeedbackResponseDto.FeedbackDetail getFeedbackByMeetingId(UUID meetingId) {
        FeedbackEntity feedback = feedbackRepository.findByMeetingId(meetingId)
                                                    .orElseThrow(() -> new BusinessException(ErrorCode.FEEDBACK_NOT_FOUND));

        return FeedbackResponseDto.FeedbackDetail.builder()
                                                 .meetingId(meetingId)
                                                 .actionItems(
                                                         feedback.getActionItems().stream()
                                                                 .map(ai -> FeedbackResponseDto.FeedbackDetail.ActionItem.builder()
                                                                                                                         .name(ai.getName())
                                                                                                                         .content(ai.getContent())
                                                                                                                         .orderIndex(ai.getOrderIndex())
                                                                                                                         .build())
                                                                 .collect(Collectors.toList())
                                                 )
                                                 .topics(
                                                         feedback.getTopics().stream()
                                                                 .map(tp -> FeedbackResponseDto.FeedbackDetail.Topic.builder()
                                                                                                                    .title(tp.getTitle())
                                                                                                                    .importance(tp.getImportance())
                                                                                                                    .summary(tp.getSummary())
                                                                                                                    .proportion(tp.getProportion())
                                                                                                                    .build())
                                                                 .collect(Collectors.toList())
                                                 )
                                                 .followUpCategories(
                                                         feedback.getFollowUpCategories().stream()
                                                                 .map(fc -> FeedbackResponseDto.FeedbackDetail.FollowUpCategory.builder()
                                                                                                                               .category(fc.getCategory())
                                                                                                                               .questions(fc.getQuestions().stream()
                                                                                                                                            .map(q -> FeedbackResponseDto.FeedbackDetail.FollowUpCategory.Question.builder()
                                                                                                                                                                                                                  .question(q.getQuestion())
                                                                                                                                                                                                                  .orderIndex(q.getOrderIndex())
                                                                                                                                                                                                                  .build())
                                                                                                                                            .collect(Collectors.toList()))
                                                                                                                               .build())
                                                                 .collect(Collectors.toList())
                                                 )
                                                 .build();
    }
}
