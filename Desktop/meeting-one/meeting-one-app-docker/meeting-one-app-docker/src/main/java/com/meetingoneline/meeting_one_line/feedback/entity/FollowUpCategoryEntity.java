package com.meetingoneline.meeting_one_line.feedback.entity;

import com.meetingoneline.meeting_one_line.global.entity.BaseEntity;
import jakarta.persistence.*;
import lombok.*;

import java.util.ArrayList;
import java.util.List;

@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
@Table(name = "follow_up_categories")
public class FollowUpCategoryEntity extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "feedback_id", nullable = false)
    private FeedbackEntity feedback;

    @Column(nullable = false, length = 255)
    private String category;

    @OneToMany(mappedBy = "category", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<FollowUpQuestionEntity> questions = new ArrayList<>();

    public static FollowUpCategoryEntity create(FeedbackEntity feedback, String category) {
        FollowUpCategoryEntity entity = new FollowUpCategoryEntity();
        entity.feedback = feedback;
        entity.category = category;
        return entity;
    }
}
