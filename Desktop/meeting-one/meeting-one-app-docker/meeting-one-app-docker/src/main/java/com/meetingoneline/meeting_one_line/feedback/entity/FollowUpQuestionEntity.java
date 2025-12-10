package com.meetingoneline.meeting_one_line.feedback.entity;

import com.meetingoneline.meeting_one_line.global.entity.BaseEntity;
import jakarta.persistence.*;
import lombok.*;

@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
@Table(name = "follow_up_questions")
public class FollowUpQuestionEntity extends BaseEntity {

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "category_id", nullable = false)
    private FollowUpCategoryEntity category;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String question;

    @Column(name = "order_index")
    private Integer orderIndex;

    public static FollowUpQuestionEntity create(FollowUpCategoryEntity category, String question, int orderIndex) {
        FollowUpQuestionEntity entity = new FollowUpQuestionEntity();
        entity.category = category;
        entity.question = question;
        entity.orderIndex = orderIndex;
        return entity;
    }
}
