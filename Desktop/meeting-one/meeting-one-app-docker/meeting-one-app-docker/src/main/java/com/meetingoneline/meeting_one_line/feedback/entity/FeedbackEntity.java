package com.meetingoneline.meeting_one_line.feedback.entity;

import com.meetingoneline.meeting_one_line.global.entity.BaseEntity;
import com.meetingoneline.meeting_one_line.meeting.entity.MeetingEntity;
import jakarta.persistence.*;
import lombok.*;

import java.util.ArrayList;
import java.util.List;

@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
@Table(name = "feedbacks")
public class FeedbackEntity extends BaseEntity {

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "meeting_id", nullable = false, unique = true)
    private MeetingEntity meeting;

    @OneToMany(mappedBy = "feedback", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<ActionItemEntity> actionItems = new ArrayList<>();

    @OneToMany(mappedBy = "feedback", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<TopicEntity> topics = new ArrayList<>();

    @OneToMany(mappedBy = "feedback", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<FollowUpCategoryEntity> followUpCategories = new ArrayList<>();

    public static FeedbackEntity create(MeetingEntity meeting) {
        FeedbackEntity feedback = new FeedbackEntity();
        feedback.meeting = meeting;
        return feedback;
    }
}

