package com.meetingoneline.meeting_one_line.meeting.entity;

import com.meetingoneline.meeting_one_line.global.entity.BaseEntity;
import jakarta.persistence.*;
import lombok.*;

import java.util.ArrayList;
import java.util.List;

@Setter
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@Entity
@Table(name = "speakers")
public class SpeakerEntity extends BaseEntity {
    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "meeting_id", nullable = false)
    private MeetingEntity meeting;

    @Column(name = "speaker_id", nullable = false, length = 10)
    private String speakerId; // 예: S1, S2, 임시 별칭

    @Column(length = 100)
    private String name;    // 유저가 지정한 이름

    @OneToMany(mappedBy = "speaker", cascade = CascadeType.ALL, orphanRemoval = true)
    private List<SegmentEntity> segments = new ArrayList<>();

    public static SpeakerEntity create(MeetingEntity meeting, String speakerId, String name){
        SpeakerEntity speaker = new SpeakerEntity();
        speaker.meeting = meeting;
        speaker.speakerId = speakerId;
        speaker.name = name;

        return speaker;
    }
}
